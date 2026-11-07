#!/usr/bin/env python3
"""评测员校准：把评测员的答案和人工标注（rubrics/labels/<清单名>.yaml）逐题比对，看一致率。

    uv run --project mcp python eval/agent/calibrate.py s4-warning-rejected [--llm-cache-only]

输出：
- 逐题一致率（评测员答案 == 人工标注）；
- 用例级判定一致率：评测员判“通过/不通过”（答案都落在 expect 里）与人工标注判的是否相同。这是评测员实际起作用的地方；
- 每个不一致的题：样本、人工、评测员、评测员的引文和问题。

标注文件里 kind 为 real 的样本取真实会话的回复；kind 以 synthetic 开头的样本用 reply 覆盖回复（证据仍取自 session）。
一致率不够就不要启用这个清单。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grade  # noqa: E402
import judge  # noqa: E402


class WithReply:
    """真实会话，但最后回复换成指定文字（合成样本）。"""

    def __init__(self, root, reply: str):
        self._root, self._reply = root, reply

    def final_reply(self) -> str:
        return self._reply

    def __getattr__(self, name):
        return getattr(self._root, name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rubric")
    a = ap.parse_args()
    rubric = judge.load_rubric(a.rubric)
    spec = yaml.safe_load((judge.RUBRICS / "labels" / f"{a.rubric}.yaml").read_text(encoding="utf-8"))
    cfg = judge.judge_config()
    print(f"评测员模型 {cfg['model']}（{cfg['base_url']}），样本 {len(spec['samples'])} 份，{len(rubric.questions)} 题")

    exact = {q.id: [0, 0] for q in rubric.questions}
    verdict = {"agree": 0, "total": 0, "human_fail_caught": [0, 0], "human_pass_kept": [0, 0]}
    rows = []
    for s in spec["samples"]:
        root = grade.load_tree(s["session"])
        if s.get("reply"):
            root = WithReply(root, s["reply"])
        reply = root.final_reply()
        text = judge.cached_call(judge.build_messages(rubric, judge.build_evidence(root), reply), None)
        ans = judge.parse_answers(text, reply, rubric)
        for q in rubric.questions:
            exact[q.id][1] += 1
            exact[q.id][0] += ans[q.id]["valid"] and ans[q.id]["value"] == s["labels"][q.id]
            if not (ans[q.id]["valid"] and ans[q.id]["value"] == s["labels"][q.id]):
                rows.append((s["id"], s["kind"], q.id, s["labels"][q.id], ans[q.id]))
        human_ok = all(s["labels"][q.id] in q.expect for q in rubric.questions)
        judge_ok = not judge.compare(rubric, ans)
        verdict["total"] += 1
        verdict["agree"] += human_ok == judge_ok
        key = "human_pass_kept" if human_ok else "human_fail_caught"
        verdict[key][1] += 1
        verdict[key][0] += human_ok == judge_ok

    print("\n逐题一致率（评测员答案 == 人工标注）")
    for qid, (k, n) in exact.items():
        print(f"  {qid:<22} {k}/{n}")
    print("\n用例级判定（答案都落在 expect 里 = 通过）")
    print(f"  一致 {verdict['agree']}/{verdict['total']}")
    print(f"  人工判不通过的样本，评测员也判不通过 {verdict['human_fail_caught'][0]}/{verdict['human_fail_caught'][1]}（漏判 = 放过了坏回复）")
    print(f"  人工判通过的样本，评测员也判通过     {verdict['human_pass_kept'][0]}/{verdict['human_pass_kept'][1]}（误判 = 冤枉了好回复）")
    if rows:
        print("\n不一致的题")
        for sid, kind, qid, human, ans in rows:
            extra = f"；{ans['problem']}" if ans["problem"] else ""
            print(f"  [{sid} {kind}] {qid}: 人工 {human!r}，评测员 {ans['value']!r}{extra}" + (f"；引文：{ans['quote'][:60]}" if ans["quote"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
