#!/usr/bin/env python3
"""智能体端到端评测：按 cases.yaml 设置模拟矿井、通过 DSH 网页端向总指挥协调提问、等会话结束后按规则判定。

DSH 0.2.1-rc.4 的 headless / ACP 入口都不支持 Agent 预设，所以经网页端驱动（Playwright 无头浏览器），
判定读 .dsh-home/sessions 下的会话日志（grade.py）。

前提：make up（或 scripts/dev.sh）已启动 MCP 服务和 DSH 网页端；RESCUE_MCP_TOKEN 与服务一致。

    uv run --project mcp --with playwright python eval/agent/run.py --url 'http://127.0.0.1:3080/?token=…' \\
        --model deepseek-official/deepseek-flash --repeat 3

--url 是 DSH 启动时打印的带 token 地址（也可设 RESCUE_DSH_URL）。--model 取网页模型下拉里的
提供方/模型 id，不给则用当前默认模型；结束后恢复原来的默认模型。
每次运行的判定写入 var/eval/agent/<时间>/results.jsonl。

注意：评测会重置模拟矿井场景（manual 时钟），并在 DSH 里留下评测会话。不要在演示进行中运行。
用例的 approvals 会放行指令（如封闭方案），放行的指令写入指令台账和审计日志：跑这类用例要用 `make dev-eval`
启动，把台账和审计写到 var/eval/ 下，不要写进复盘用的 var/actions-ledger.jsonl、var/audit/。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml
from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grade  # noqa: E402

ROOT = grade.ROOT
CASES = Path(__file__).resolve().parent / "cases.yaml"
SIM = os.environ.get("RESCUE_SIM_CONTROL_URL", "http://127.0.0.1:8101/sim").rstrip("/")
# 模拟矿井控制令牌，与 rescue_contract.serving.sim_control_token 一致
TOKEN = os.environ.get("RESCUE_SIM_CONTROL_TOKEN") or os.environ.get("RESCUE_MCP_MINE_DATA_TOKEN") or os.environ.get("RESCUE_MCP_TOKEN")


def sim(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{SIM}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def setup_sim(case: dict) -> tuple[str, dict[str, str]]:
    """重置场景为手动时钟（评测期间不走），按 apply 在指定时刻施加处置，快进到用例时刻。

    返回（矿井时间, 提问模板变量）：since 是第一次处置的矿井时间（精确到秒）。"""
    sim("/scenario", {"id": case["scenario"], "manual": True})
    t, fmt = 0.0, {}
    for act in sorted(case.get("apply", []), key=lambda a: a["at_h"]):
        if act["at_h"] > t:
            sim("/advance", {"hours": act["at_h"] - t})
            t = act["at_h"]
        body = {"type": act["type"], "params": act.get("params", {}), "roadways": act.get("roadways", []), "source": "eval"}
        fmt.setdefault("since", sim("/interventions", body)["applied_at"][:19])
    now = sim("/advance", {"hours": float(case.get("at_h", 0)) - t})["now"]
    return now, fmt


# ── 网页端操作 ──────────────────────────────────────────────────────────────

MODEL_BUTTON = 'button[aria-label^="Select model"]'


def new_session(page: Page) -> None:
    page.get_by_role("button", name="New Session").first.click()
    page.locator("[data-composer-input]").wait_for()


def current_model(page: Page) -> str | None:
    page.locator(MODEL_BUTTON).click()
    page.get_by_role("menuitem", name="Model").click()
    sel = page.locator('[role=menuitemradio][aria-checked=true] [data-provider-id]').first
    out = f"{sel.get_attribute('data-provider-id')}/{sel.get_attribute('data-model-id')}" if sel.count() else None
    page.keyboard.press("Escape")
    return out


def select_model(page: Page, model: str) -> None:
    provider, _, model_id = model.partition("/")
    page.locator(MODEL_BUTTON).click()
    page.get_by_role("menuitem", name="Model").click()
    item = page.locator(f'[role=menuitemradio]:has([data-provider-id="{provider}"][data-model-id="{model_id}"])')
    if not item.count():
        page.keyboard.press("Escape")
        raise SystemExit(f"网页模型下拉里没有 {model}")
    item.first.click()
    page.keyboard.press("Escape")


def send(page: Page, prompt: str) -> None:
    box = page.locator("[data-composer-input]")
    box.click()
    page.keyboard.type(prompt)
    page.keyboard.press("Enter")


# ── 等待会话结束 ────────────────────────────────────────────────────────────


def find_root(prompt: str, since_ms: int, timeout_s: float) -> str:
    """按“提问消息的发送时间”找会话，不按会话创建时间：网页端点 New Session 会复用之前留下的空会话。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for p in grade._session_files(grade.SESSIONS):
            if not p.parent.name.startswith("session-") or p.stat().st_mtime * 1000 < since_ms:
                continue
            s = grade.load_session(p)
            first = next((e for e in s.events if e["type"] == "user/message" and (e["data"].get("source") or {}).get("kind") == "user"), None)
            if s.preset == "rescue-commander" and first and first["time"] >= since_ms and s.inputs()[:1] == [prompt]:
                return s.id
        time.sleep(2)
    raise TimeoutError("没有找到评测会话")


def wait_settled(root_id: str, page: Page, timeout_s: float, quiet_s: float, approvals: dict[str, str] | None = None) -> tuple[grade.Session, str]:
    """全部会话的回合都结束、且 quiet_s 秒内没有新事件，视为结束（settled）；
    根会话停在 ask_user_question 等人回答时也视为结束（asked_user）。

    人工确认按 approvals（actions 工具短名 → allow / reject）点 Allow once 或 Reject；没列出的工具一律拒绝，
    无人值守时不会一直挂着，也不会误放行。"""
    approvals = approvals or {}
    deadline = time.time() + timeout_s
    last_count, last_change = -1, time.time()
    while time.time() < deadline:
        root = grade.load_tree(root_id)
        sessions = list(root.walk())
        count = sum(len(s.events) for s in sessions)
        if count != last_count:
            last_count, last_change = count, time.time()
        waiting = [a for a in root.approvals() if a["outcome"] is None]
        if waiting:
            name = "Allow once" if approvals.get(waiting[0]["tool"]) == "allow" else "Reject"
            btn = page.get_by_role("button", name=name)
            if btn.count():
                btn.first.click()
                time.sleep(3)  # 等 approval/decided 写入日志，避免重复点击
                continue
        # 总指挥委派后结束本轮等结果时，子 Agent 结束到总指挥被唤醒之间所有会话都暂时“已结束”：
        # 子 Agent 的最后回合比总指挥的最后回合结束得晚，说明它的结论还没被总指挥处理，继续等
        pending = any(c.last_time("turn/end") > root.last_time("turn/end") for c in sessions if c is not root)
        if all(s.turns_settled() for s in sessions) and not pending and time.time() - last_change >= quiet_s:
            return root, "settled"
        # 指挥 Agent 停下来问人（ask_user_question）也是正常收尾：评测不代答，按已有输出判定
        # 子 Agent 还在工作时提问（如“要不要先给出要点”）不算收尾，等子 Agent 结束
        children_done = all(c.turns_settled() for c in sessions if c is not root)
        if children_done and any(n == "ask_user_question" for n, _ in _unanswered(root)) and time.time() - last_change >= quiet_s:
            return root, "asked_user"
        time.sleep(3)
    return grade.load_tree(root_id), "timeout"


def _unanswered(s: grade.Session) -> list[tuple[str, str]]:
    done = {e["data"]["message"].get("toolCallId") for e in s.events if e["type"] == "tool/result"}
    return [(e["data"]["name"], e["data"]["callId"]) for e in s.events if e["type"] == "tool/call" and e["data"]["callId"] not in done]


# ── 主流程 ─────────────────────────────────────────────────────────────────


def run_one(page: Page, case: dict, model: str | None, timeout_s: float, quiet_s: float) -> dict:
    mine_now, fmt = setup_sim(case)
    prompt = case["prompt"].format(**fmt)
    new_session(page)
    if model:
        select_model(page, model)
    t0 = int(time.time() * 1000)
    send(page, prompt)
    root_id = find_root(prompt, t0 - 5000, 120)
    root, status = wait_settled(root_id, page, timeout_s, quiet_s, case.get("approvals"))
    r = grade.grade(root, case["checks"])
    r.update(case=case["id"], requested_model=model, mine_now=mine_now, seconds=round(time.time() - t0 / 1000))
    if r["llm_errors"]:
        status = "llm_error"
    r["status"] = status
    if status in ("timeout", "llm_error"):
        r["passed"] = False
    return r


VALID = ("settled", "asked_user")  # 模型调用失败或超时的运行不计入通过率


def summarize(results: list[dict]) -> None:
    print("\n== 汇总（通过次数 / 有效运行次数；模型调用失败、超时的运行另计）")
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in results:
        groups.setdefault((r["case"], r["requested_model"] or r["model"] or "?"), []).append(r)
    for (case, model), runs in groups.items():
        rs = [r for r in runs if r["status"] in VALID]
        invalid = ", ".join(f"{r['status']} {sum(x['status'] == r['status'] for x in runs)}" for r in {x["status"]: x for x in runs if x not in rs}.values())
        line = f"{case:<18} {model:<36} 全部通过 {sum(r['passed'] for r in rs)}/{len(rs)}"
        if rs:
            line += "  " + "  ".join(f"{n} {sum(r['checks'][n]['ok'] for r in rs)}/{len(rs)}" for n in rs[0]["checks"])
            line += f"  平均 {sum(s['tokens'] for r in rs for s in r['sessions']) // len(rs)} token"
        print(line + (f"  （另有 {invalid}）" if invalid else ""))


def regrade(path: Path) -> list[dict]:
    """用当前的判定规则重新判定已保存的运行（不重新提问）。"""
    cases = {c["id"]: c for c in yaml.safe_load(CASES.read_text(encoding="utf-8"))}
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        old = json.loads(line)
        root = grade.load_tree(old["root"])
        r = grade.grade(root, cases[old["case"]]["checks"])
        status = old["status"]
        if status == "timeout" and any(n == "ask_user_question" for n, _ in _unanswered(root)) and all(c.turns_settled() for c in root.walk() if c is not root):
            status = "asked_user"
        if r["llm_errors"]:
            status = "llm_error"
        r.update({k: old[k] for k in ("case", "requested_model", "mine_now", "seconds")}, status=status)
        if status in ("timeout", "llm_error"):
            r["passed"] = False
        out.append(r)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get("RESCUE_DSH_URL"), help="DSH 网页端地址（带 token）")
    ap.add_argument("--model", action="append", default=[], help="提供方/模型 id，可重复")
    ap.add_argument("--case", action="append", default=[], help="只跑这些用例 id，可重复")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=900, help="单次运行超时秒数")
    ap.add_argument("--quiet", type=float, default=20, help="无新事件多少秒视为结束")
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    ap.add_argument("--regrade", type=Path, help="不提问，只按当前规则重新判定这个 results.jsonl")
    a = ap.parse_args(argv)
    if a.regrade:
        results = regrade(a.regrade)
        for r in results:
            print(f"== {r['case']}  {r['requested_model']}  {r['status']}")
            grade.print_report(r)
        summarize(results)
        return 0 if all(r["passed"] for r in results) else 1
    if not a.url:
        ap.error("需要 --url 或 RESCUE_DSH_URL")

    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    if a.case:
        cases = [c for c in cases if c["id"] in a.case]
    out_dir = ROOT / "var" / "eval" / "agent" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not a.headed)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(a.url)
        new_session(page)
        original = current_model(page)
        try:
            for case in cases:
                for model in a.model or [None]:
                    for i in range(a.repeat):
                        print(f"== {case['id']}  {model or original}  第 {i + 1}/{a.repeat} 次", flush=True)
                        r = run_one(page, case, model, a.timeout, a.quiet)
                        results.append(r)
                        with (out_dir / "results.jsonl").open("a", encoding="utf-8") as f:
                            f.write(json.dumps(r, ensure_ascii=False) + "\n")
                        grade.print_report(r)
                        print(f"   {r['status']}，{r['seconds']} 秒，矿井时间 {r['mine_now']}" + (f"，模型调用失败：{r['llm_errors'][0]}" if r["llm_errors"] else ""), flush=True)
        finally:
            if original and a.model:
                new_session(page)
                select_model(page, original)  # 网页端选模型会改写配置档的默认模型，恢复原样
            browser.close()
    summarize(results)
    print(f"\n明细：{out_dir.relative_to(ROOT)}/results.jsonl")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
