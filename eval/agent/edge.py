#!/usr/bin/env python3
"""值班会话的边界情况（需要 Docker 演示环境，make demo-up；环境变量同 story.py 在 Docker 里的用法）。

A 操作员正在对话时又来了告警：智能体处理第一条告警的过程中，操作员插话，同时（矿井时间推进）又有新告警投递进来。
  期望：两条都被处理（新告警有研判记录、操作员的话有回复），会话正常结束，没有卡死。
B DSH 进程重启之后又来告警（冷会话恢复）：
  期望：唤醒插件按记下的会话编号恢复值班会话，新告警被投递并处理。

    python eval/agent/edge.py --url '<DSH 地址带 token>' --container <DSH 容器名>
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as runner  # noqa: E402
import story  # noqa: E402
from rescue_contract.incidents import IncidentStore  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--container", required=True, help="DSH 容器名（B 用 docker restart 重启它）")
    ap.add_argument("--timeout", type=float, default=600)
    a = ap.parse_args(argv)
    incident_dir = Path(os.environ["RESCUE_INCIDENT_DIR"])
    store = IncidentStore(incident_dir)
    results: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        results.append((name, bool(ok)))
        story.say(f"  {'PASS' if ok else 'FAIL'}  {name}")

    runner.sim("/demo/normal", {"speed": 60})
    story.wait_for(lambda: (incident_dir / "sentinel" / "heartbeat.json").exists(), 60, "监听器心跳")
    for v in store.list():
        store.append(v["incident_id"], v.get("updated_at") or "2026-01-01T00:00:00", "status", "edge", {"status": "closed", "reason": "清理"})
    runner.sim("/demo/inject", {"scenario": "s3-accelerating", "speed": 60})
    runner.sim("/pause", {"paused": True})

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.add_init_script("try { localStorage.setItem('rescue-map:disabled', '1') } catch (e) {}")  # 用 DSH 原界面，不用地图界面
        page.goto(a.url)
        ctx: dict = {}

        def settle() -> None:
            root, status = runner.wait_settled(ctx["sid"], page, a.timeout, 20, story.APPROVALS)
            for _ in range(4):
                if status != "asked_user":
                    break
                story.answer_questions(page)
                root, status = runner.wait_settled(ctx["sid"], page, a.timeout, 20, story.APPROVALS)
            ctx["root"], ctx["status"] = root, status
            story.say(f"  值班会话 {status}")

        story.say("准备：推进到黄色，等值班会话处理完")
        runner.sim("/advance", {"hours": 35.5})
        iid = story.wait_for(lambda: story.has_alert(store, "黄色"), 300, "黄色告警")
        n = story.delivered(store, iid)
        story.wait_for(lambda: n >= 0 and story.delivered(store, iid) >= 1, 180, "投递")
        ctx["sid"] = story.wait_for(lambda: story.duty_session(incident_dir), 120, "值班会话")
        story.open_duty_session(page)
        settle()

        # ── A：处理告警的过程中操作员插话，同时又来新告警 ──
        story.say("A：新告警（橙色）刚被投递、智能体开始处理时，操作员插话")
        d0 = story.delivered(store, iid)
        before = story.count(store, iid)
        runner.sim("/advance", {"hours": 2})
        story.wait_for(lambda: story.delivered(store, iid) > d0, 180, "橙色告警投递")
        runner.send(page, "顺便问一句：现在 1201 采空区最不利测点在哪个位置？只回答位置，不要提交任何指令。")
        settle()
        user_msgs = [e for e in ctx["root"].events if e["type"] == "user/message" and (e["data"].get("source") or {}).get("kind") == "user"]
        check("A：会话正常结束（没有超时、没有卡在提问）", ctx["status"] == "settled")
        check("A：新告警被处理（有橙色研判记入时间线）", story.notes(store, iid, before, "assessment") >= 1)
        check("A：操作员插话被回复（用户消息之后有智能体回复）", bool(user_msgs) and any(e["type"] == "turn/end" and e["time"] > user_msgs[-1]["time"] for e in ctx["root"].events))

        # ── B：DSH 进程重启之后又来告警 ──
        story.say("B：重启 DSH 容器，再推进矿井时间触发新告警（跟踪计划到点或升红）")
        subprocess.run(["docker", "restart", a.container], check=True, capture_output=True)
        story.wait_for(lambda: subprocess.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", a.container], capture_output=True, text=True).stdout.strip() == "healthy", 300, "DSH 容器健康", poll=5)
        d1 = story.delivered(store, iid)
        before = story.count(store, iid)
        runner.sim("/advance", {"hours": 24})
        story.wait_for(lambda: story.delivered(store, iid) > d1, 300, "重启后的告警被投递")
        check("B：重启后新告警被投递到值班会话", True)
        page.goto(a.url)
        page.wait_for_timeout(3000)
        story.open_duty_session(page)
        ctx["sid"] = story.duty_session(incident_dir)
        settle()
        check("B：重启后告警被处理（时间线有新的研判或复评）", story.notes(store, iid, before, "assessment") + story.notes(store, iid, before, "effect_review") >= 1)
        browser.close()

    print("\n== 边界情况结果")
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
