#!/usr/bin/env python3
"""智能体端到端评测的判定：读 DSH 会话日志（.dsh-home/sessions），按用例里的规则逐项判定。

只用标准库，可以对任何已有会话离线判定（比如验证清单里手工跑过的会话）：

    python3 eval/agent/grade.py session-75e32fa5-...                # 默认规则
    python3 eval/agent/grade.py session-75e32fa5-... --json

一次运行 = 根会话（总指挥协调）+ 它委派出的子会话（日志头里 parentSession 指向父会话）。

规则（用例 checks 里的键）：
- delegates_to: <预设>      某个子会话用的是这个预设
- no_actions: true          整次运行没有调用 actions 的任何工具
- numbers_grounded: true    每个会话输出的数值都能在它看到的材料里找到（见 check_numbers）
- cited_tools_called: true  输出里点名的 MCP 服务或工具，在本会话或其上下级会话里确实调用过
- stage_reported: true      总指挥协调的最后回复里写了 assess_warning_level 最近一次给出的自燃发展阶段（stage_name）

数值溯源是启发式的，局限见 eval/README.md。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSIONS = ROOT / ".dsh-home" / "sessions"
SERVERS = ("mine_data", "ventnet", "fire_model", "dispatch", "kb", "actions")
# 模型看到的输入里，算作“材料”的消息来源；运行时上下文、技能目录等系统注入不算
INPUT_KINDS = {"user", "agent-message", "subagent-settled"}


# ── 会话日志 ────────────────────────────────────────────────────────────────


@dataclass
class Session:
    id: str
    preset: str | None
    parent: str | None
    created_at: int
    events: list[dict]
    children: list[Session] = field(default_factory=list)

    @property
    def model(self) -> str | None:
        """实际应答的模型（取自 assistant 消息；用默认模型时日志里没有 model/selection）。"""
        for e in self.events:
            if e["type"] == "assistant/message":
                src = e["data"]["message"].get("source") or {}
                if src.get("provider"):
                    return f"{src['provider']}/{src.get('model')}"
        return None

    def calls(self) -> list[tuple[str, str]]:
        """(工具名, 参数原文)。"""
        return [(e["data"]["name"], e["data"].get("arguments") or "") for e in self.events if e["type"] == "tool/call"]

    def tool_results(self) -> list[tuple[str, str]]:
        """(工具名, 结果文本)。"""
        names = {e["data"]["callId"]: e["data"]["name"] for e in self.events if e["type"] == "tool/call"}
        out = []
        for e in self.events:
            if e["type"] != "tool/result":
                continue
            msg = e["data"]["message"]
            text = "\n".join(c.get("text", "") for c in msg.get("content", []) if c.get("type") == "text")
            out.append((names.get(msg.get("toolCallId"), "?"), text))
        return out

    def inputs(self) -> list[str]:
        out = []
        for e in self.events:
            if e["type"] == "user/message" and (e["data"].get("source") or {}).get("kind") in INPUT_KINDS:
                out.append("\n".join(c.get("text", "") for c in e["data"]["content"] if c.get("type") == "text"))
        return out

    def outputs(self) -> list[str]:
        """模型写给人或别的 Agent 的文字：assistant 的 text 块，以及 delegate / send_message 的消息正文。"""
        out = []
        for e in self.events:
            if e["type"] != "assistant/message":
                continue
            for c in e["data"]["message"]["content"]:
                if c.get("type") == "text" and c["text"].strip():
                    out.append(c["text"])
        for name, args in self.calls():
            if name in ("delegate", "send_message"):
                try:
                    a = json.loads(args)
                except ValueError:
                    continue
                out += [a[k] for k in ("prompt", "message") if isinstance(a.get(k), str)]
        return out

    def final_text(self) -> str:
        """最后一条带文字的 assistant 消息。"""
        for e in reversed(self.events):
            if e["type"] == "assistant/message":
                text = "".join(c["text"] for c in e["data"]["message"]["content"] if c.get("type") == "text")
                if text.strip():
                    return text
        return ""

    def turns_settled(self) -> bool:
        starts = sum(e["type"] == "turn/start" for e in self.events)
        ends = sum(e["type"] == "turn/end" for e in self.events)
        return starts > 0 and starts == ends

    def tokens(self) -> int:
        return sum((e["data"].get("usage") or {}).get("totalTokens", 0) for e in self.events if e["type"] == "assistant/message")

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def read_events(path: Path) -> list[dict]:
    from compression import zstd  # Python 3.14+

    raw = zstd.decompress(path.read_bytes()).decode("utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def _session_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/*/session.v4.jsonl.zstd"))


def load_session(path: Path) -> Session:
    events = read_events(path)
    head = events[0]
    return Session(head["id"], head.get("agentPreset"), head.get("parentSession"), head.get("createdAt", 0), events)


def load_tree(root_id: str, sessions_dir: Path = SESSIONS) -> Session:
    """读根会话及全部下级会话。只解压同一工作区目录下、创建时间不早于根会话的日志。"""
    files = [p for p in _session_files(sessions_dir) if p.parent.name == root_id]
    if not files:
        raise FileNotFoundError(f"找不到会话 {root_id}（{sessions_dir}）")
    root = load_session(files[0])
    by_id = {root.id: root}
    for p in sorted(files[0].parent.parent.glob("*/session.v4.jsonl.zstd")):
        if p.parent.name == root_id or p.stat().st_mtime * 1000 < root.created_at:
            continue
        s = load_session(p)
        if s.created_at >= root.created_at:
            by_id[s.id] = s
    for s in by_id.values():
        if s.parent in by_id and s is not root:
            by_id[s.parent].children.append(s)
    return root


# ── 数值溯源 ────────────────────────────────────────────────────────────────

# 不用 \b：中文字符也算 \w，“使G1”里的 G1 前面没有词边界
_STRIP = [
    re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?"),  # 日期时间
    re.compile(r"(?<![\d.-])\d{1,2}-\d{1,2}(?=[T\s]*\d{1,2}:\d{2})"),  # 月-日（后跟时刻，如 11-03 06:00、11-04T08:00）
    re.compile(r"(?<![\d.])\d{1,2}:\d{2}(?::\d{2})?(?![\d.])"),  # 时刻
    re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_‐‑‒–—][A-Za-z0-9]+)+"),  # 编号：BT-1201-G1、P-13、call_xxx（含 U+2011 等连字符）
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]+\d+[A-Za-z0-9]*"),  # G1、C2H4、CO2、s2
    re.compile(r"第\s*\d+(?:\s*[、,，/／]\s*\d+)*\s*[条节章步轮项]"),  # 条号、节号：第85条、第 75/164 条
    re.compile(r"(?m)^\s*#+\s*\d+(?:\.\d+)*[.、]?\s"),  # 标题序号：## 5.、### 2.1
    re.compile(r"(?m)^\s*[(（]?\d+[.、)）]\s"),  # 列表序号
    re.compile(r"#\d+"),  # 验证项、用例编号
]
_NUM = re.compile(r"\d+(?:\.\d+)?")


def numbers(text: str) -> list[tuple[str, int]]:
    """(数值原文, 在去掉编号等之后的文本中的位置)。"""
    for rx in _STRIP:
        text = rx.sub(lambda m: " " * len(m.group(0)), text)
    return [(m.group(0), m.start()) for m in _NUM.finditer(text)]


def match_level(token: str, pool: set[str]) -> str:
    """exact：材料里有这个数，或按报告的小数位四舍五入后相等；
    approx：报告写的整数，材料里有四舍五入到它的数（如 43.998 → 44），列为提醒；
    none：材料里找不到。整数容易碰巧对上，approx 只是提醒，不能证明有依据。"""
    if token in pool:
        return "exact"
    decimals = len(token.split(".")[1]) if "." in token else 0
    want = float(token)
    values = [float(x) for x in pool]
    if any((v == want) if decimals == 0 else (round(v, decimals) == want) for v in values):
        return "exact"
    if decimals == 0 and any(abs(v - want) < 0.5 for v in values):
        return "approx"
    return "none"


def check_numbers(s: Session) -> dict[str, list[dict]]:
    """{"none": [...], "approx": [...]}，每项含数值和上下文。"""
    pool: set[str] = set()
    for text in [t for _, t in s.tool_results()] + [a for _, a in s.calls()] + s.inputs():
        pool.update(tok for tok, _ in numbers(text))
    out: dict[str, list[dict]] = {"none": [], "approx": []}
    for text in s.outputs():
        for tok, pos in numbers(text):
            level = match_level(tok, pool)
            if level != "exact":
                out[level].append({"value": tok, "context": text[max(0, pos - 30) : pos + 30].replace("\n", " ")})
    return out


# ── 点名的来源是否真的调用过 ────────────────────────────────────────────────


def _catalog(s: Session) -> set[str]:
    """会话里出现过的 MCP 工具全名（含提供给模型的工具定义）。"""
    raw = json.dumps(s.events, ensure_ascii=False)
    return set(re.findall(r"mcp__[a-z_]+?__[a-z_]+", raw))


_NOT_DONE = re.compile(r"未做|未调用|没有调用|未执行|没有执行|无法调用")


def uncalled_citations(s: Session, lineage_calls: set[str], result_keys: set[str] = frozenset()) -> list[str]:
    """输出里点名的服务（如 ventnet）或工具（如 threatened_personnel），在本会话及其上下级会话里都没有调用过。

    不算点名：名字同时是已调用工具结果里的字段（如 threatened_personnel 结果里的 escape_route）；
    名字前后 30 字内写明“未做/未调用”等（如实说明没做，不是冒称依据）。"""
    tools = {name.split("__", 2)[2]: name for name in _catalog(s)}
    called_servers = {n.split("__")[1] for n in lineage_calls if n.startswith("mcp__")}
    called_tools = {n.split("__", 2)[2] for n in lineage_calls if n.startswith("mcp__")}
    text = "\n".join(s.outputs())
    missing = []
    for server in SERVERS:
        if server == "kb" or server == "actions":  # 太短或常作普通词，不按名字点名判定
            continue
        # 前面不能是连字符：rescue-dispatch 是预设名，不是在引用 dispatch 服务
        if re.search(rf"(?<![A-Za-z_-]){server}(?![A-Za-z_-])", text) and server not in called_servers:
            missing.append(server)
    for tool, full in tools.items():
        if full.startswith("mcp__actions__") or tool in result_keys:  # 发布类工具通常是在建议下发指令；同名字段不是引用
            continue
        if tool in called_tools:
            continue
        hits = [m for m in re.finditer(rf"(?<![A-Za-z_]){tool}(?![A-Za-z_])", text)]
        if any(not _NOT_DONE.search(text[max(0, m.start() - 30) : m.end() + 30]) for m in hits):
            missing.append(tool)
    return sorted(set(missing))


# ── 判定 ───────────────────────────────────────────────────────────────────

DEFAULT_CHECKS = {"numbers_grounded": True, "cited_tools_called": True}


def grade(root: Session, checks: dict) -> dict:
    sessions = list(root.walk())
    parents = {c.id: s for s in sessions for c in s.children}
    all_calls = [n for s in sessions for n, _ in s.calls()]
    results: dict[str, dict] = {}

    if checks.get("delegates_to"):
        want = checks["delegates_to"]
        got = [c.preset for c in sessions if c is not root]
        results["delegates_to"] = {"ok": want in got, "detail": got}

    if checks.get("no_actions"):
        used = sorted({n for n in all_calls if n.startswith("mcp__actions__")})
        results["no_actions"] = {"ok": not used, "detail": used}

    if checks.get("numbers_grounded"):
        detail, approx = {}, {}
        for s in sessions:
            r = check_numbers(s)
            if r["none"]:
                detail[f"{s.preset}:{s.id[-8:]}"] = r["none"]
            if r["approx"]:
                approx[f"{s.preset}:{s.id[-8:]}"] = r["approx"]
        results["numbers_grounded"] = {"ok": not detail, "detail": detail, "approx": approx}

    if checks.get("cited_tools_called"):
        detail = {}
        for s in sessions:
            lineage = {n for x in s.walk() for n, _ in x.calls()}
            p = parents.get(s.id)
            while p is not None:
                lineage |= {n for n, _ in p.calls()}
                p = parents.get(p.id)
            keys = {k for x in sessions for name, t in x.tool_results() if name.startswith("mcp__") for k in re.findall(r'"([a-z_]+)"\s*:', t)}
            if missing := uncalled_citations(s, lineage, keys):
                detail[f"{s.preset}:{s.id[-8:]}"] = missing
        results["cited_tools_called"] = {"ok": not detail, "detail": detail}

    if checks.get("stage_reported"):
        names = [
            json.loads(text).get("stage_name")
            for s in sessions
            for name, text in s.tool_results()
            if name == "mcp__fire_model__assess_warning_level" and text.lstrip().startswith("{")
        ]
        want = next((n for n in reversed(names) if n), None)
        final = root.final_text()
        results["stage_reported"] = {"ok": want is not None and want in final, "detail": {"tool": want}}

    return {
        "root": root.id,
        "model": root.model,
        "sessions": [{"id": s.id, "preset": s.preset, "model": s.model, "tokens": s.tokens()} for s in sessions],
        "settled": all(s.turns_settled() for s in sessions),
        # 回合因模型调用失败（连接错误等）结束：这次运行测的是端点而不是 Agent
        "llm_errors": [
            ((reason.get("error") or {}).get("message") or "?")
            for x in sessions
            for e in x.events
            if e["type"] == "turn/end" and (reason := (e.get("data") or {}).get("reason") or {}).get("kind") == "error"
        ],
        "checks": results,
        "passed": all(r["ok"] for r in results.values()),
    }


def print_report(r: dict) -> None:
    print(f"会话 {r['root']}  模型 {r['model']}  {'已结束' if r['settled'] else '未结束'}")
    for s in r["sessions"]:
        print(f"  {s['preset'] or '?':<20} {s['model'] or '（无模型回复）':<40} {s['tokens']:>8} token")
    for name, c in r["checks"].items():
        print(f"{'PASS' if c['ok'] else 'FAIL'}  {name}")
        for who, items in (c.get("approx") or {}).items():
            print(f"      提醒 {who} 整数近似：{'、'.join(it['value'] for it in items)}")
        if c["ok"]:
            continue
        d = c["detail"]
        if isinstance(d, dict):
            for who, items in d.items():
                if not isinstance(items, list):
                    print(f"      {who}: {items}")
                    continue
                print(f"      {who}:")
                for it in items:
                    print(f"        {it['value']:>8}  …{it['context']}…" if isinstance(it, dict) else f"        {it}")
        else:
            print(f"      {d}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session", help="根会话 id（总指挥协调的会话）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sessions-dir", type=Path, default=SESSIONS)
    a = ap.parse_args(argv)
    r = grade(load_tree(a.session, a.sessions_dir), DEFAULT_CHECKS)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print_report(r)
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
