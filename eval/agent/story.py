#!/usr/bin/env python3
"""演示故事线脚本（第三版 P5，docs/design-v3.md 第 9 节）：整条走通，不是单点评测。

需要 `make dev-story` 起的环境（模拟矿井演示模式 + 监听器 + DSH，数据写到 var/eval/）。这里扮演操作员：
控制模拟矿井（常态 → 注入事故 → 推进时钟），在网页端值班会话里按步骤说话、按策略点人工确认；
判定看事件库里的时间线（监听器、唤醒插件、流程、guard 写的条目），不看模型说了什么。

    make story ARGS="--url '<DSH 地址带 token>'"

步骤（与设计第 9 节对应；启封审批、事件关闭后的封闭流程第三版没有，见 CLAUDE.md）：
1 常态：监听器在运行，没有未关闭事件
2 注入事故：监听器发现异常，开事件、告警
3 无人界面唤醒值班会话，智能体自己研判；对外动作（预警、升级）弹人工确认，按策略放行
4 操作员决定封闭：编制封闭方案、合规审核、人工确认下发，智能体登记跟踪计划
5 效果跟踪：推进矿井时间到跟踪计划到点，监听器唤醒智能体复评
7 复盘：review_timeline 生成时间线，次数和时长照抄

通过条件见 check_timeline：事件时间线里依次出现告警、已投递、研判、对外动作（至少一次下发成功）、跟踪计划登记和到点、效果复评。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grade  # noqa: E402
import run as runner  # noqa: E402
from rescue_contract.incidents import IncidentStore  # noqa: E402

ROOT = grade.ROOT
APPROVALS = {"publish_warning": "allow", "escalate_emergency": "allow", "issue_sealing_plan": "allow"}  # 其他确认一律拒绝


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_for(cond, timeout_s: float, what: str, poll: float = 2.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        got = cond()
        if got:
            return got
        time.sleep(poll)
    raise TimeoutError(f"等待超时（{timeout_s:g} 秒）：{what}")


def kinds(store: IncidentStore, iid: str) -> list[tuple[str, str | None, str | None]]:
    """（kind, note 类型, 动作结果）"""
    out = []
    for e in store.entries(iid):
        d = e.get("data") or {}
        out.append((e["kind"], d.get("type") if e["kind"] == "note" else None, d.get("status") if d.get("type") == "action" else None))
    return out


def check_timeline(store: IncidentStore, iid: str) -> list[tuple[str, bool]]:
    ks = kinds(store, iid)
    has = lambda kind, note=None, status=None: any(k == kind and (note is None or n == note) and (status is None or s == status) for k, n, s in ks)  # noqa: E731
    return [
        ("监听器告警", has("alert")),
        ("已投递值班会话", has("delivered")),
        ("研判记入时间线", has("note", "assessment")),
        ("对外动作记入时间线", has("note", "action")),
        ("至少一次下发成功", has("note", "action", "issued")),
        ("登记跟踪计划", has("followup_added")),
        ("跟踪计划到点", has("followup_due")),
        ("效果复评记入时间线", has("note", "effect_review")),
    ]


def duty_session(incident_dir: Path) -> str | None:
    try:
        return json.loads((incident_dir / "duty-session.json").read_text(encoding="utf-8"))["sessionId"]
    except (OSError, ValueError, KeyError):
        return None


DUTY_WORKSPACE = "值班"  # 唤醒插件在值班工作目录上建的工作区的标题（rescue-wake.workspaceTitle）
DUTY_TITLE = "值班会话（监听器告警）"  # 唤醒插件创建值班会话后显式设置的标题（cordis.patch.yml 的 rescue-wake.title），显式标题不会被自动生成覆盖


def open_duty_session(page: Page) -> None:
    page.reload()  # 值班会话是插件在后台建的，已打开的页面侧栏里没有，刷新才看得到
    page.wait_for_timeout(3000)
    # 点会话那一行（role=treeitem）；点里面的文字 span 会被会话列表的覆盖层拦住（侧栏在会话更新时重绘）
    row = page.get_by_role("treeitem").filter(has_text=DUTY_TITLE)
    if not row.count():  # 值班工作区的分组折叠着：先点开
        page.get_by_text(DUTY_WORKSPACE, exact=True).first.click(timeout=15_000)
        page.wait_for_timeout(1000)
    for _ in range(6):
        try:
            row.first.click(timeout=5_000)
            break
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(1500)
    else:
        raise RuntimeError("点不开值班会话")
    # 智能体正等人工确认时输入框是隐藏的（弹窗替代了它），这里不等输入框；后面 send 之前会话已经跑完


# 现场条件的回答（操作员替总指挥确认）：按问题里的关键词给答案；选择题点包含答案文字的选项，填空题填进输入框
FIELD_ANSWERS = [("温度", "28"), ("能见度", "20"), ("火源", "已明确"), ("水源", "充足"), ("回风", "畅通"), ("谁确认", "总指挥")]


def answer_questions(page: Page, max_questions: int = 8) -> int:
    """智能体用提问卡片（逐题“下一题”）向人要现场条件时，按 FIELD_ANSWERS 作答；返回答了几题。网页端中文界面。"""
    done = 0
    for _ in range(max_questions):
        box = page.get_by_placeholder("输入你的答案")
        card = page.locator("text=/现场条件 \\d+\\/\\d+/")
        if not card.count() and not box.count():
            break
        question = page.locator("text=/\\?$|？$/").last.inner_text() if page.locator("text=/\\?$|？$/").count() else ""
        answer = next((a for k, a in FIELD_ANSWERS if k in question), None)
        if answer is None:
            break
        option = page.get_by_text(answer, exact=False)
        if box.count() and box.first.is_visible():
            box.first.fill(answer)
        elif option.count():
            option.first.click()
        for label in ("下一题", "提交", "Submit"):
            btn = page.locator("button:not([disabled])").filter(has_text=label)  # 卡片上还有同名的图标按钮（aria-label，没有文字，答完才可用），要点带文字的那个
            if btn.count():
                btn.first.click()
                break
        done += 1
        page.wait_for_timeout(1500)
    return done


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get("RESCUE_DSH_URL"), help="DSH 网页端地址（带 token）")
    ap.add_argument("--speed", type=float, default=600, help="注入事故时的模拟倍速（600 = 1 真实秒 10 矿井分钟）")
    ap.add_argument("--timeout", type=float, default=900, help="每一步等待智能体的秒数")
    ap.add_argument("--quiet", type=float, default=20)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args(argv)
    if not a.url:
        ap.error("需要 --url 或 RESCUE_DSH_URL")
    incident_dir = Path(os.environ.get("RESCUE_INCIDENT_DIR") or ROOT / "var" / "eval" / "incidents")
    store = IncidentStore(incident_dir)

    say("步骤 1 常态：监听器运行、没有未关闭事件")
    runner.sim("/demo/normal", {"speed": 60})
    hb = wait_for(lambda: (incident_dir / "sentinel" / "heartbeat.json").exists(), 60, "监听器心跳")
    assert hb
    for v in store.list():  # 上一次演示的残留事件关掉，免得混进来
        store.append(v["incident_id"], v.get("updated_at") or "2026-01-01T00:00:00", "status", "story", {"status": "closed", "reason": "演示开始前清理"})
    time.sleep(5)
    assert store.list() == [], "常态下不应有事件"

    say(f"步骤 2 注入事故（倍速 {a.speed:g}）：等监听器发现异常")
    runner.sim("/demo/inject", {"scenario": "s4-escalation", "speed": a.speed})
    inc = wait_for(lambda: next(iter(store.list()), None), 600, "监听器开事件")
    iid = inc["incident_id"]
    say(f"  监听器开了事件 {iid}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not a.headed)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(a.url)

        say("步骤 3 唤醒值班会话：智能体自己研判，对外动作按策略放行")
        sid = wait_for(lambda: duty_session(incident_dir), 120, "值班会话被唤醒")
        open_duty_session(page)
        root, status = runner.wait_settled(sid, page, a.timeout, a.quiet, APPROVALS)
        say(f"  值班会话 {status}")
        if status == "asked_user":  # 智能体研判后主动起草了封闭方案，向人要现场条件（缺信息，允许提问）：操作员作答
            say(f"  智能体提问现场条件，操作员作答 {answer_questions(page)} 题")
            root, status = runner.wait_settled(sid, page, a.timeout, a.quiet, APPROVALS)
            say(f"  值班会话 {status}")

        say("步骤 4 操作员决定封闭：编制封闭方案、合规审核、人工确认下发")
        runner.send(page, "1201 采空区火势无法控制，指挥部决定封闭 1201 采空区火区。现场条件由总指挥确认：灾区温度 28 ℃，能见度 20 米，火源已明确，水源、人力和物力充足，回风道畅通。请编制封闭方案，经合规审核通过后提交人工确认下发。")
        root, status = runner.wait_settled(sid, page, a.timeout, a.quiet, APPROVALS)
        say(f"  值班会话 {status}")
        if not store.get(iid)["followups"]:
            say("  智能体还没有登记跟踪计划，提醒一次")
            runner.send(page, "请为这次处置登记复评（跟踪计划）。")
            root, status = runner.wait_settled(sid, page, a.timeout, a.quiet, APPROVALS)

        say("步骤 5 效果跟踪：推进矿井时间到跟踪计划到点")
        pending = [f for f in store.get(iid)["followups"] if f["state"] == "pending"]
        if pending:
            runner.sim("/pause", {"paused": True})
            now = runner.sim("/advance", {"hours": 0})["now"]
            due = max(f["due_at"] for f in pending)
            gap_h = (time.mktime(time.strptime(due[:19], "%Y-%m-%dT%H:%M:%S")) - time.mktime(time.strptime(now[:19], "%Y-%m-%dT%H:%M:%S"))) / 3600
            if gap_h > 0:
                runner.sim("/advance", {"hours": gap_h + 0.1})
            say(f"  推进到 {due} 之后")
        wait_for(lambda: any(f["state"] in ("due", "done") for f in store.get(iid)["followups"]), 120, "监听器写 followup_due")
        wait_for(lambda: len([k for k in kinds(store, iid) if k[0] == "delivered"]) >= 2, 120, "跟踪到点的告警被投递")
        root, status = runner.wait_settled(sid, page, a.timeout, a.quiet, APPROVALS)
        say(f"  值班会话 {status}")

        say("步骤 7 复盘：review_timeline")
        runner.send(page, f"请复盘事件 {iid}，总结从发现异常到现在的处置过程。")
        root, status = runner.wait_settled(sid, page, a.timeout, a.quiet, APPROVALS)
        reviewed = "review_timeline" in [n for n, _ in root.calls()]
        browser.close()

    results = [*check_timeline(store, iid), ("复盘调用 review_timeline", reviewed)]
    print("\n== 故事线结果")
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"事件 {iid}：{store.get(iid)['status_name']}；条目 {store.get(iid)['entry_count']} 条（{store.dir}）")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
