#!/usr/bin/env python3
"""导演驱动的演示（第五版）：只在控制台“开始演示”一次，智能体研判、在卡片上问操作员选处置，导演按矿井实际执行的处置推进。

这里扮演操作员：POST /sim/demo/start，之后每次导演停在某一级等选择时，在网页端值班会话里回答提问卡片、点确认，
直到导演结束。判定看事件库时间线和导演的记录。

    make demo-up 之后：
    RESCUE_SIM_CONTROL_URL=http://127.0.0.1:13090/sim RESCUE_INCIDENT_DIR=var/demo/incidents \\
    DSH_HOME=var/demo/dsh-home RESCUE_DSH_URL='<网页端地址带 token>' STORY_ORANGE_OPTIONS='注氮|注浆|堵漏' \\
    uv run --project mcp --with playwright python eval/agent/demo_run.py

STORY_ORANGE_OPTIONS：橙色卡片上选哪些（| 分隔；注氮|注浆|堵漏 = 3 项，回落；选两项 = 要求追加；只选一项 = 升红；不采取处置 = 在输入框输入这句话，明确不处置，升红；空串 = 点跳过，暂不决定，导演继续等）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("STORY_ORANGE_OPTIONS", "注氮|注浆|堵漏")  # 演示脚本默认三项全选（走回落分支）；story 模块读取这个环境变量
import run as runner  # noqa: E402
import story  # noqa: E402
from rescue_contract.incidents import IncidentStore  # noqa: E402

say, answer_questions, APPROVALS = story.say, story.answer_questions, story.APPROVALS



def runner_get(path: str) -> dict:
    import json
    import urllib.request

    req = urllib.request.Request(f"{runner.SIM}{path}", headers={"Authorization": f"Bearer {runner.TOKEN}"} if runner.TOKEN else {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("RESCUE_DSH_URL"))
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--quiet", type=float, default=20)
    a = ap.parse_args(argv)
    incident_dir = Path(os.environ.get("RESCUE_INCIDENT_DIR") or story.ROOT / "var" / "eval" / "incidents")
    store = IncidentStore(incident_dir)
    for v in store.list():  # 上一次的残留事件关掉；新的值班会话
        store.append(v["incident_id"], v.get("updated_at") or "2026-01-01T00:00:00", "status", "demo_run", {"status": "closed", "reason": "演示开始前清理"})
    (incident_dir / "duty-session.json").unlink(missing_ok=True)
    runner.sim("/demo/normal", {"speed": 60})
    time.sleep(6)
    results: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        results.append((name, bool(ok)))
        say(f"  {'PASS' if ok else 'FAIL'}  {name}")

    runner.sim("/demo/start", {})
    say("已点“开始演示”")
    sid: str | None = None
    with sync_playwright() as pw:
        page: Page = pw.chromium.launch(headless=True).new_page(viewport={"width": 1400, "height": 1000})
        page.add_init_script("try { localStorage.setItem('rescue-map:disabled', '1') } catch (e) {}")
        page.goto(a.url)
        t0, last_stage = time.time(), None
        while time.time() - t0 < 1500:
            d = runner_get("/demo/director")
            if d["state"] == "finished":
                break
            if d["state"] == "holding" and d["stage"] != last_stage:
                last_stage = d["stage"]
                say(f"导演停在{d['stage']}，等操作员选择（智能体研判、卡片、下发）")
            if d["state"] == "holding":
                if sid is None:
                    sid = story.duty_session(incident_dir)
                    if sid:
                        story.open_duty_session(page)
                if sid:
                    try:
                        runner.wait_settled(sid, page, 120, 15, APPROVALS)
                        n = answer_questions(page)
                        if n:
                            say(f"  智能体提问，操作员作答 {n} 题")
                    except Exception as exc:  # noqa: BLE001
                        say(f"  等待会话出错：{type(exc).__name__}")
            time.sleep(2)
        d = runner_get("/demo/director")
    say(f"导演状态：{d['state']}；结果：{d.get('outcome')}")
    for e in d["log"]:
        say(f"  {e['at']} {e['text']}")
    iid = max(store.list(include_closed=True), key=lambda v: store.entries(v["incident_id"])[-1]["wall"])["incident_id"]  # 最后一条记录最新的事件 = 这一次演示的（矿井日期每次相同，不能按矿井时间挑）
    acts = story.actions(store, iid)
    check("导演走完", d["state"] == "finished")
    check("黄色：加密监测已下发", ("start_monitoring", "issued") in acts)
    if os.environ["STORY_ORANGE_OPTIONS"].strip() == "不采取处置":  # 明确不处置：不编方案、不下发，导演走 declined 分支
        check("橙色：操作员不采取处置，没有下发防灭火方案", ("issue_firefighting_plan", "issued") not in acts and any(c.get("declined") for c in d.get("chosen", [])))
    else:
        check("橙色：防灭火方案已下发", ("issue_firefighting_plan", "issued") in acts)
    check("橙色：处置选项卡片问过操作员（get_response_options 之后）", any(c["stage"] == "橙色" for c in d.get("chosen", [])))
    print("\n".join(f"  {t} {s}" for t, s in acts))
    print("== 结果")
    for n, ok in results:
        print(("PASS  " if ok else "FAIL  ") + n)
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
