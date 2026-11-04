#!/usr/bin/env python3
"""演示彩排（design-v2 第 6 节、docs/demo-script.md）：在 Docker 演示环境里按演示脚本从头走一遍，人工操作由本脚本代做。

代做的人工操作：演示控制台的按钮（开始演示、快进）、指挥人员的每句话、回答智能体的提问、审批弹窗点“允许”。
其余都是系统自己完成的：每一步结束后按会话日志（grade.py 的规则）、模拟矿井状态、指令台账和审计日志判定，
任何一项不通过都算这一步失败（验收：除人工操作外无失败）。

    make demo-reset                         # 演示前：清空演示数据，模拟矿井回到常态
    uv run --project mcp --with playwright python eval/agent/rehearse.py --url "$(scripts/demo.sh url)"

--reset 先自己执行 scripts/demo.sh reset 并取网页端地址。结果写到 var/demo/rehearsals/<时间>.json；
彩排会话留在演示环境的 DSH 里（正式演示前再 make demo-reset）。会放行指令：只能对演示环境运行（台账、审计在 var/demo/）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "var" / "demo"
os.environ.setdefault("RESCUE_DSH_SESSIONS", str(DEMO / "dsh-home" / "sessions"))
os.environ.setdefault("RESCUE_SIM_CONTROL_URL", f"http://127.0.0.1:{os.environ.get('DEMO_CONSOLE_PORT', '13090')}/sim")


def _demo_tokens() -> dict[str, str]:
    p = DEMO / "tokens.env"
    return dict(line.split("=", 1) for line in p.read_text().split() if "=" in line) if p.exists() else {}


os.environ.setdefault("RESCUE_SIM_CONTROL_TOKEN", _demo_tokens().get("RESCUE_SIM_CONTROL_TOKEN", ""))

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grade  # noqa: E402
import run  # noqa: E402
from playwright.sync_api import Page, sync_playwright  # noqa: E402


def sim_get(path: str) -> dict:
    req = urllib.request.Request(f"{run.SIM}{path}", headers={"Authorization": f"Bearer {run.TOKEN}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def ledger() -> list[dict]:
    p = DEMO / "ledger" / "actions-ledger.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def audit() -> list[dict]:
    p = DEMO / "audit" / "tool-actions.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


# ── 演示步骤（第 6 节）────────────────────────────────────────────────────

INJECT = {"scenario": "s4-escalation", "speed": 600, "advance_h": 54}  # 开始演示：注入后快进 54 矿井小时（红色），倍速 600
EFFECT_ADVANCE_H = 24  # 第 6 步：下发封闭方案后快进的矿井小时


@dataclass
class Step:
    id: str
    title: str
    say: str | None = None  # 指挥人员说的话
    console: dict | None = None  # 演示控制台的操作：{"inject": {...}} 或 {"advance": 小时}
    answer: str | None = None  # 回答智能体的提问（选项文字）
    wake: bool = False  # 等后台任务结束唤醒智能体
    prefer: list[str] = field(default_factory=list)  # 智能体提了脚本没写到的问题时，优先选含这些文字的选项（演示脚本里写明的回答）
    approvals: dict[str, str] = field(default_factory=dict)
    checks: dict = field(default_factory=dict)  # 只看这一步的事件
    cumulative: dict = field(default_factory=dict)  # 看到这一步为止的全部事件（数值溯源、记录引用要用前面步骤的材料）


GROUNDED = {"numbers_grounded": True, "cited_tools_called": True}
STEPS = [
    Step("0a", "常态：一次性检测", say="看一下现在矿井是否有异常", checks={"tools_called": ["check_mine"], "no_actions": True}, cumulative=GROUNDED),
    Step("0b", "常态：开始持续检测", say="持续检测矿井动态，有异常的时候告诉我", checks={"tools_called": ["check_mine"], "no_actions": True}, cumulative=GROUNDED),
    Step("1", "开始演示：注入事故，持续检测发现异常，智能体询问", console={"inject": INJECT}, wake=True, checks={"watch_asked": True, "no_actions": True}, cumulative=GROUNDED),
    Step(
        "2", "火灾研判", answer="开始完整研判",
        checks={"tools_called": ["run_fire_assessment"], "report_pasted": True, "explanation_generated": True, "stage_reported": True, "no_actions": True},
        cumulative=GROUNDED,
    ),  # fmt: skip
    Step(
        "3", "发布预警、升级响应", say="发布红色预警并升级应急响应",
        approvals={"publish_warning": "allow", "escalate_emergency": "allow"},
        checks={"approval_asked": ["publish_warning", "escalate_emergency"], "actions_issued": ["publish_warning", "escalate_emergency"]},
        cumulative={**GROUNDED, "snapshot_referenced": True},
    ),  # fmt: skip
    Step(
        "4", "编制封闭方案", say="决定封闭 1201 采空区，编制方案",
        prefer=["暂不下发", "按现有方案", "先不下发"],  # 现场条件缺数据时问怎么处理：演示里不补数据，由确认人在第 5 步的审批弹窗里把关
        checks={"tools_called": ["draft_sealing_plan"], "report_pasted": True, "no_actions": True}, cumulative=GROUNDED,
    ),  # fmt: skip
    Step(
        "5", "下发封闭方案", say="同意，下发封闭方案", approvals={"issue_sealing_plan": "allow"},
        checks={"approval_asked": ["issue_sealing_plan"], "actions_issued": ["issue_sealing_plan"]},
        cumulative={**GROUNDED, "snapshot_referenced": True},
    ),  # fmt: skip
    Step(
        "6", "复评封闭效果", console={"advance": EFFECT_ADVANCE_H}, say="评估封闭效果",
        checks={"tools_called": ["review_effect"], "report_pasted": True, "effect_reported": True, "no_actions": True}, cumulative=GROUNDED,
    ),  # fmt: skip
    Step("6b", "继续持续检测（可选）", say="继续持续检测", checks={"tools_called": ["check_mine"], "no_actions": True}, cumulative=GROUNDED),
]


# ── 每一步的事件切片 ──────────────────────────────────────────────────────


def view(root: grade.Session, start: int, end: int | None = None) -> grade.Session:
    """根会话第 start 个事件起（到 end）的部分，加上这段时间里新建的下级会话（阶段子 Agent）。"""
    ev = root.events[start:end]
    t0 = root.events[start]["time"] if start < len(root.events) and "time" in root.events[start] else 0
    t1 = ev[-1].get("time", 0) if ev else 0
    s = grade.Session(root.id, root.preset, root.parent, root.created_at, [root.events[0], *ev] if start else ev)
    s.children = [c for c in root.children if t0 <= c.created_at <= max(t1, t0)] if start else list(root.children)
    return s


def extra_checks(step: Step, root: grade.Session, start: int, before: dict) -> dict[str, dict]:
    """会话日志以外的判定：工具结果、模拟矿井状态、台账和审计。"""
    out: dict[str, dict] = {}
    results = [(n, t) for n, t in view(root, start).tool_results() if n == "check_mine" and t.lstrip().startswith("{")]
    parsed = [json.loads(t) for _, t in results]
    if step.id == "0a":
        once = [r for r in parsed if r.get("mode", "once") == "once" and "anomaly" in json.dumps(r)]
        ok = bool(once) and all(not (r.get("result") or r).get("anomaly") for r in once)
        out["check_normal"] = {"ok": ok, "detail": [((r.get("card") or {}).get("summary") or "")[:120] for r in once]}
    if step.id in ("0b", "6b"):
        started = [r for r in parsed if r.get("mode") == "watch" and r.get("started")]
        out["watch_started"] = {"ok": bool(started), "detail": [r.get("job_id") for r in started] or [x.get("note") for x in parsed]}
    st = sim_get("/state")
    if step.id == "1":
        hits = st["anomalies"][len(before["anomalies"]) :]
        out["sim_auto_paused"] = {"ok": bool(hits) and st["paused"], "detail": {"paused": st["paused"], "anomalies": [h["reasons"] for h in hits]}}
    if step.id == "2":
        levels = [
            ((json.loads(t).get("result") or {}).get("warning") or {}).get("level_name")
            for n, t in view(root, start).tool_results()
            if n == "run_fire_assessment" and t.lstrip().startswith("{")
        ]
        out["level_red"] = {"ok": any(x and "红" in x for x in levels), "detail": levels}
    if step.approvals:
        new = ledger()[before["ledger"] :]
        want = [t for t, v in step.approvals.items() if v == "allow"]
        got = [r.get("tool") for r in new]
        out["ledger_written"] = {"ok": all(t in got for t in want), "detail": got}
        aud = audit()[before["audit"] :]
        out["audit_written"] = {"ok": len(aud) > 0, "detail": len(aud)}
    if step.id == "5":
        acts = [a["type"] for a in st["actions"][len(before["actions"]) :]]
        out["sim_executed"] = {"ok": bool(acts), "detail": acts}
    return out


def snapshot(_root: grade.Session | None) -> dict:
    st = sim_get("/state")
    return {"anomalies": st["anomalies"], "actions": st["actions"], "ledger": len(ledger()), "audit": len(audit())}


# ── 主流程 ─────────────────────────────────────────────────────────────────


def wait_root(prompt: str, since_ms: int) -> str:
    return run.find_root(prompt, since_ms, 120)


def do_step(page: Page, step: Step, root_id: str | None, timeout_s: float, quiet_s: float, shots: Path) -> tuple[str | None, dict]:
    t0 = time.time()
    root = grade.load_tree(root_id) if root_id else None
    start = len(root.events) if root else 0
    turns = sum(e["type"] == "turn/start" for e in root.events) if root else 0
    before = snapshot(root)
    status = "settled"
    if step.console and "inject" in step.console:
        run.sim("/demo/inject", step.console["inject"])
    if step.console and "advance" in step.console:
        run.sim("/advance", {"hours": float(step.console["advance"])})
    if step.answer:
        if not run.answer_question(page, step.answer):
            status = "answer_failed"
        time.sleep(5)
    if step.say:
        since = int(time.time() * 1000)
        run.send(page, step.say)
        if root_id is None:
            root_id = wait_root(step.say, since - 5000)
    if step.wake and not run.wait_new_turn(root_id, turns, 300):
        status = "no_wake"
    answered = []
    if status == "settled":
        root, status = run.wait_settled(root_id, page, timeout_s, quiet_s, step.approvals)
        # 智能体问了脚本没写到的问题（如方案缺人工确认项）：按 prefer 选，没有就选第一个，记下来（属于人工操作）
        while status == "asked_user" and step.id != "1" and len(answered) < 3:
            q = [json.loads(a or "{}") for n, a in root.calls() if n == "ask_user_question"][-1:]
            opts = [o.get("label") for x in q for qq in x.get("questions", []) for o in qq.get("options", [])]
            pick = next((o for p in step.prefer for o in opts if p in o), opts[0] if opts else None)
            if not pick or not run.answer_question(page, pick):
                status = "answer_failed"
                break
            answered.append(pick)
            time.sleep(5)
            root, status = run.wait_settled(root_id, page, timeout_s, quiet_s, step.approvals)
    root = grade.load_tree(root_id)
    sl = view(root, start)
    r = grade.grade(sl, step.checks) if step.checks else {"checks": {}, "llm_errors": []}
    checks = dict(r["checks"])
    if step.cumulative:
        checks.update(grade.grade(view(root, 0), step.cumulative)["checks"])
    checks.update(extra_checks(step, root, start, before))
    if step.id == "1":
        ok = status == "asked_user"
        checks["asked_operator"] = {"ok": ok, "detail": status}
        if ok:
            status = "settled"
    elif status == "asked_user":
        status = "settled"  # 以向人提问结束：回答由下一步给出
    errors = r.get("llm_errors") or []
    passed = status == "settled" and not errors and all(c["ok"] for c in checks.values())
    if not passed:
        page.screenshot(path=str(shots / f"step-{step.id}.png"), full_page=True)
    tokens = sum(s.tokens() for s in sl.walk())
    return root_id, {
        "step": step.id, "title": step.title, "status": status, "passed": passed, "seconds": round(time.time() - t0),
        "tokens": tokens, "answered": answered, "llm_errors": errors, "checks": checks, "mine_now": sim_get("/state")["now"],
    }  # fmt: skip


URL_RE = re.compile(r"http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]+")


def demo_url(text: str = "") -> str:
    """网页端地址：demo.sh 的输出里有就用；否则读 dsh 容器日志（compose 要 .env 里的变量，直接 docker logs）。"""
    if not URL_RE.search(text):
        out = subprocess.run(["docker", "logs", "rescue-demo-dsh-1"], capture_output=True, text=True, check=False)
        text = out.stdout + out.stderr
    urls = URL_RE.findall(text)
    if not urls:
        raise SystemExit("找不到网页端地址（scripts/demo.sh logs）")
    return urls[-1]


def dismiss_notice(page: Page) -> None:
    """预览声明（Preview Notice）正常不会弹出（入口脚本的用户层 patch 已确认过）；万一弹出就点 Continue，不挡住后面的操作。"""
    btn = page.locator('[role="dialog"] button', has_text="Continue")
    try:
        btn.first.wait_for(timeout=8000)
        btn.first.click()
    except Exception:  # noqa: BLE001  没有弹出就算了
        pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", help="网页端地址（带 token）；缺省从 dsh 容器日志里取")
    ap.add_argument("--reset", action="store_true", help="先执行 scripts/demo.sh reset")
    ap.add_argument("--timeout", type=float, default=900, help="每一步的超时秒数")
    ap.add_argument("--quiet", type=float, default=20)
    ap.add_argument("--only", help="只跑到这一步（如 2）")
    a = ap.parse_args(argv)
    if a.reset:
        out = subprocess.run(["scripts/demo.sh", "reset"], cwd=ROOT, check=True, capture_output=True, text=True)
        print(out.stdout[-400:])
        run.TOKEN = _demo_tokens().get("RESCUE_SIM_CONTROL_TOKEN", "")
    url = a.url or demo_url(out.stdout if a.reset else "")
    st = sim_get("/state")
    if st["mode"] != "demo" or st["injected"]:
        raise SystemExit("模拟矿井不在常态（先 make demo-reset 或控制台“重置为常态”）")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = DEMO / "rehearsals"
    shots = out_dir / f"{stamp}-shots"
    shots.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    root_id = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(url)
        dismiss_notice(page)
        run.new_session(page)
        try:
            for step in STEPS:
                print(f"== 第 {step.id} 步 {step.title}", flush=True)
                root_id, r = do_step(page, step, root_id, a.timeout, a.quiet, shots)
                results.append(r)
                for name, c in r["checks"].items():
                    print(f"   {'PASS' if c['ok'] else 'FAIL'}  {name}" + ("" if c["ok"] else f"  {json.dumps(c.get('detail'), ensure_ascii=False)[:300]}"))
                print(f"   {r['status']}，{r['seconds']} 秒，{r['tokens']} token，矿井时间 {r['mine_now'][:19]}" + (f"，代答 {r['answered']}" if r["answered"] else ""), flush=True)
                if r["status"] != "settled" or step.id == a.only:
                    break
        finally:
            browser.close()
            (out_dir / f"{stamp}.json").write_text(json.dumps({"session": root_id, "steps": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = len(results) == len(STEPS) and all(r["passed"] for r in results)
    print(f"\n== 彩排{'通过' if ok else '未通过'}：{sum(r['passed'] for r in results)}/{len(STEPS)} 步，"
          f"共 {sum(r['seconds'] for r in results)} 秒、{sum(r['tokens'] for r in results)} token；会话 {root_id}")  # fmt: skip
    print(f"明细：{(out_dir / f'{stamp}.json').relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
