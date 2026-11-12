#!/usr/bin/env python3
"""演示故事线脚本（第三版 P5，docs/design-v3.md 第 9 节）：整条走通，不是单点评测。

需要 `make dev-story` 起的环境（模拟矿井演示模式 + 监听器 + DSH，数据写到 var/eval/）。这里扮演操作员：
控制模拟矿井（常态 → 注入事故 → 推进时钟），在网页端值班会话里按步骤说话、按策略点人工确认；
判定看事件库里的时间线（监听器、唤醒插件、流程、guard 写的条目），不看模型说了什么。

    make story ARGS="--url '<DSH 地址带 token>'"

主线（场景 s3，按预警等级分段推进：每段先暂停时钟、推进到该等级、等监听器告警和智能体处理完，再进入下一段）：
1 常态：监听器在运行，没有未关闭事件
2 黄色：监听器告警、唤醒值班会话；智能体研判、告知操作员、加密监测（确认下发）、登记跟踪，不提交其他对外动作（分级决策表）
3 橙色：智能体研判后编制防灭火方案、预警，经人工确认下发
4 红色：预警、（建议时）升级、撤人通知；封闭方案经合规审核、人工确认下发
5 封闭后：跟踪计划到点，监听器唤醒智能体复评
6 启封：推进到具备启封条件，智能体评估、启封方案经人工确认下发
7 复盘：review_timeline
8 收尾：操作员确认处置结束，智能体完成跟踪计划、关闭事件

橙色处置后 s3 是否走到红色取决于橙色卡片上选了什么（STORY_ORANGE_OPTIONS，| 分隔的选项文字：默认“注氮”= 只注氮，约 70 小时升红；“注氮|注浆|堵漏”则受控；
“不采取处置” = 在输入框输入这句话提交（明确不处置）；空串 = 点跳过（暂不决定，不下发任何处置））；受控时 48 矿井小时后由操作员注入“漏风突增”
（顶板垮落，模拟矿井的 leak_change，系数 3）推到红色，并在输出里写明。漏风突增系数过大（8）会让封闭后的火区几百小时都降不到启封温度，演示选小一点。
分级决策表是占位草案（skills/fire-incident-management/SKILL.md），这里的检查点按它写；表改了这里跟着改。
复燃分支（启封后）不在这条主线里。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grade  # noqa: E402
import run as runner  # noqa: E402
from rescue_contract.incidents import IncidentStore  # noqa: E402

ROOT = grade.ROOT
APPROVALS = {"start_monitoring": "allow", "publish_warning": "allow", "escalate_emergency": "allow", "issue_firefighting_plan": "allow", "issue_sealing_plan": "allow", "notify_evacuation": "allow", "approve_unsealing": "allow"}  # 其他确认一律拒绝


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


def actions(store: IncidentStore, iid: str, since: int = 0) -> list[tuple[str, str]]:
    """guard 记进时间线的对外动作（工具, 结果），从第 since 条之后算。"""
    out = []
    for e in store.entries(iid)[since:]:
        d = e.get("data") or {}
        if e["kind"] == "note" and d.get("type") == "action":
            out.append((d.get("tool"), d.get("status")))
    return out


def count(store: IncidentStore, iid: str) -> int:
    return len(store.entries(iid))


def has_alert(store: IncidentStore, word: str) -> str | None:
    """升为 word 的等级告警所在事件编号（任一事件）。只看 cause 为 anomaly 的告警：跟踪计划到点的摘要里
    可能带着“预警等级不升为红色”这样的期望，不是升级（同导演 alert_level，2026-11-12）。"""
    for v in store.list():
        if v["status"] == "closed":  # 上一次演示残留的已关闭事件不算
            continue
        for e in store.entries(v["incident_id"]):
            d = e.get("data") or {}
            if e["kind"] == "alert" and d.get("cause") == "anomaly" and f"升为 {word}" in str(d.get("summary", "")):
                return v["incident_id"]
    return None


def delivered(store: IncidentStore, iid: str) -> int:
    return len([e for e in store.entries(iid) if e["kind"] == "delivered"])


def notes(store: IncidentStore, iid: str, since: int, note_type: str) -> int:
    return len([e for e in store.entries(iid)[since:] if e["kind"] == "note" and (e.get("data") or {}).get("type") == note_type])


def followups_since(store: IncidentStore, iid: str, since: int) -> int:
    return len([e for e in store.entries(iid)[since:] if e["kind"] == "followup_added"])


def hours_between(a: str, b: str) -> float:
    f = lambda x: time.mktime(time.strptime(x[:19], "%Y-%m-%dT%H:%M:%S"))  # noqa: E731
    return (f(b) - f(a)) / 3600


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
FIELD_ANSWERS = [("温度", "28"), ("能见度", "20"), ("火源", "是"), ("水源", "是"), ("回风", "是"), ("谁确认", "总指挥"), ("指挥部决定", "决定封闭"), ("复评起点", "本次跟踪观测起点"), ("处置选项", os.environ.get("STORY_ORANGE_OPTIONS", "注氮"))]  # 后两项是选择题：智能体就是否封闭向指挥部请示（操作员代表指挥部决定封闭）；还没有下发处置时复评起点按推荐项

OPTION_KEYS = ("指挥部决定", "复评起点", "处置选项")  # 这些问题是选择题：点选项（输入框一直在，填进去会被当成自定义文字）


def answer_questions(page: Page, max_questions: int = 8) -> int:
    """作答时页面操作出错（卡片重绘、按钮点不动）只记一笔、不让脚本崩：返回已答的题数。"""
    try:
        return _answer_questions(page, max_questions)
    except Exception as exc:  # noqa: BLE001
        say(f"  作答提问时出错（{type(exc).__name__}：{' '.join(str(exc).split())[:260]}），本轮放弃")
        return 0


def _answer_questions(page: Page, max_questions: int) -> int:
    """智能体用提问卡片（逐题“下一题”）向人要现场条件时，按 FIELD_ANSWERS 作答；返回答了几题。网页端中文界面。"""
    done = 0
    for _ in range(max_questions):
        box = page.get_by_placeholder(re.compile("输入你的答案|Type your answer"))  # 网页端语言随环境：本机英文、Docker 中文
        if not box.count():
            break
        # 问题文字：输入框所在的提问卡片（输入框所在的 section 卡片）的文字，不含已答过的旧卡片和聊天记录
        card = box.first.locator("xpath=ancestor::section[contains(@class,'card')][1]")
        question = card.inner_text(timeout=5000)
        # 只认已知类型的提问卡片（卡片眉题 + 标题里有现场条件、确认人、封闭决定、复评起点等字样）；别的（如“缺信息：现场复核”）
        # 的问题文字里常带“回风巷”“温度”等词，按关键词硬答会答错，一律跳过，让智能体按自己的判断继续
        head = " ".join(card.locator("[class*=eyebrow], h2").all_inner_texts()) or question
        known = bool(re.search("现场条件|确认人|灾区温度|能见度|火源|水源|回风道|封闭方向|复评起点|处置选项", head))
        hits = [(head.rfind(k), a) for k, a in FIELD_ANSWERS if k in head] if known else []
        answer = max(hits)[1] if hits else None  # 同时出现多个关键词时取离输入框最近（最靠后）的那个
        if answer is None and known and "确认人" in head:  # 问现场条件确认人是谁（选项是各岗位）：总指挥确认
            answer = "总指挥"
        if answer is None or answer == "":  # 答案为空串：多选卡片一项都不选，只能点“跳过”（提交按钮是灰的；算暂不决定）
            # 不认识的问题：记下问题文字（事后分析智能体问了什么），点“跳过”让智能体按自己的判断继续，免得脚本卡在提问卡片上
            say(f"  {'未识别的提问' if answer is None else '操作员暂不决定'}（跳过）：{' '.join(question.split())[-120:]}")
            try:
                card.get_by_role("button", name=re.compile("^(Skip|跳过)$")).first.click(timeout=5000)
            except Exception:  # noqa: BLE001  “跳过”点不动时关掉整张提问卡片（右上角“Dismiss all questions”）
                try:
                    card.get_by_role("button", name=re.compile("Dismiss|关闭|忽略")).first.click(timeout=5000)
                except Exception:  # noqa: BLE001
                    say("  提问卡片关不掉，放弃作答")
                    break
            done += 1
            page.wait_for_timeout(1500)
            continue
        option = card.get_by_text(answer, exact=False)
        if answer == "不采取处置" and "处置选项" in head:  # 处置选项卡片上明确不处置：不勾选项，在输入框输入这句话再提交（问题文字里也有这几个字，不能按文字点）
            box.first.fill(answer, timeout=5000)
        elif any(k in head for k in OPTION_KEYS):  # 选择题：点选项（输入框一直在，填进去会被当成自定义文字）；选项文字对不上就点第 1 项（推荐项）
            if "|" in answer:  # 多选卡片（处置选项）：逐个点选；答案为空串表示一个都不选
                for part in [x for x in answer.split("|") if x]:
                    card.get_by_text(part, exact=False).first.click(timeout=5000)
            else:
                (option if option.count() else card.get_by_text(re.compile(r"^1$"))).first.click(timeout=5000)
        elif (exact := card.get_by_text(answer, exact=True)).count():  # 卡片上有这个选项（是/否、确认人、数值档位）：点选项
            exact.first.click(timeout=5000)
        elif box.count() and box.first.is_visible():  # 没有对应选项（如温度 28）：在“其他”里输入
            box.first.fill(answer, timeout=5000)
        elif option.count():
            option.first.click(timeout=5000)
        for label in ("下一题", "提交", "Next", "Submit"):
            # 卡片上还有同名的图标按钮（aria-label，没有文字，答完才可用），要点带文字的那个；先在提问卡片里按文字整段匹配找，
            # 找不到再找全页：聊天记录里工具活动的标题按钮也会含“提交”二字（如“提交预警”），按包含匹配会点到它、点不动
            exact = re.compile(rf"^\s*{label}\s*$")
            btn = card.locator("button:not([disabled])").filter(has_text=exact)
            if not btn.count():
                btn = page.locator("button:not([disabled])").filter(has_text=exact)
            if btn.count():
                btn.first.click(timeout=5000)
                break
        done += 1
        page.wait_for_timeout(1500)
    return done




_panel: str | None = None


def panel() -> str:
    """综采工作面的名称：提问里要跟着模拟矿井的名字（RESCUE_SIM_PANEL，Docker 演示缺省 211016），否则智能体会去评估一个不存在的工作面。

    环境变量 RESCUE_SIM_PANEL / DEMO_PANEL 优先；没设就问模拟矿井（/sim/state 的 panel），不用每次手动带；都没有按 1201。"""
    global _panel
    if _panel is None:
        _panel = os.environ.get("RESCUE_SIM_PANEL") or os.environ.get("DEMO_PANEL") or ""
        if not _panel:
            try:
                _panel = str(runner.sim_get("/state").get("panel") or "")
            except (OSError, ValueError) as e:
                say(f"  （取不到模拟矿井的工作面名，按 1201：{e}）")
        _panel = _panel or "1201"
    return _panel
SEAL_PROMPT = (
    "指挥部决定封闭 {panel} 采空区火区。现场条件由总指挥确认：灾区温度 28 ℃，能见度 20 米，火源已明确，水源、人力和物力充足，回风道畅通。"
    "请编制封闭方案，经合规审核通过后提交人工确认下发。"
)
UNSEAL_PROMPT = "{panel} 采空区火区已封闭一段时间。请评估现在是否具备启封条件；具备的话提交启封方案，等我确认。"
SEALED_HOURS = 320  # 封闭后推进到的时长（矿井小时）；s3 约 306 小时后具备启封条件（占位，随场景和判据变）


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get("RESCUE_DSH_URL"), help="DSH 网页端地址（带 token）")
    ap.add_argument("--timeout", type=float, default=900, help="每一步等待智能体的秒数")
    ap.add_argument("--quiet", type=float, default=20)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args(argv)
    if not a.url:
        ap.error("需要 --url 或 RESCUE_DSH_URL")
    incident_dir = Path(os.environ.get("RESCUE_INCIDENT_DIR") or ROOT / "var" / "eval" / "incidents")
    store = IncidentStore(incident_dir)
    results: list[tuple[str, bool]] = []
    ctx: dict = {}

    def check(name: str, ok: bool) -> None:
        results.append((name, bool(ok)))
        say(f"  {'PASS' if ok else 'FAIL'}  {name}")

    say("步骤 1 常态：监听器运行、没有未关闭事件")
    runner.sim("/demo/normal", {"speed": 60})
    wait_for(lambda: (incident_dir / "sentinel" / "heartbeat.json").exists(), 60, "监听器心跳")
    for v in store.list():  # 上一次演示的残留事件关掉，免得混进来
        store.append(v["incident_id"], v.get("updated_at") or "2026-01-01T00:00:00", "status", "story", {"status": "closed", "reason": "演示开始前清理"})
    # 每次演示从新的值班会话开始：模拟矿井时钟每次从头走，旧会话里留着上一次的矿井时间，
    # 智能体会把新告警当成“重复的历史回放”而拒绝对外动作。唤醒插件找不到 duty-session.json 就新建会话。
    (incident_dir / "duty-session.json").unlink(missing_ok=True)
    time.sleep(5)
    assert store.list() == [], "常态下不应有事件"

    # 注入后立刻暂停：之后每一段由脚本推进时钟，监听器按推进后的矿井时间检测
    runner.sim("/demo/inject", {"scenario": "s3-accelerating", "speed": 60})
    runner.sim("/pause", {"paused": True})
    say("已注入 s3-accelerating 并暂停时钟")

    def tell(page: Page, prompt: str) -> None:
        """对智能体说话。输入框被未处理的提问卡片或确认弹窗挡住时，先作答、点确认再发（最多 3 次）。"""
        for attempt in range(3):
            try:
                runner.send(page, prompt)
                return
            except Exception:  # noqa: BLE001
                say(f"  输入框不可用（第 {attempt + 1} 次），先处理待答的提问或确认")
                answer_questions(page)
                runner.wait_settled(ctx["sid"], page, 120, 10, APPROVALS)
        runner.send(page, prompt)

    def advance(hours: float) -> str:
        return runner.sim("/advance", {"hours": hours})["now"]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not a.headed)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.add_init_script("try { localStorage.setItem('rescue-map:disabled', '1') } catch (e) {}")  # 用 DSH 原界面，不用地图界面
        page.goto(a.url)

        def settle() -> str:
            root, status = runner.wait_settled(ctx["sid"], page, a.timeout, a.quiet, APPROVALS)
            for _ in range(4):  # 提问（封闭决定、现场条件）：操作员作答，可能连着几轮
                if status != "asked_user":
                    break
                say(f"  智能体提问，操作员作答 {answer_questions(page)} 题")
                root, status = runner.wait_settled(ctx["sid"], page, a.timeout, a.quiet, APPROVALS)
            say(f"  值班会话 {status}")
            ctx["root"] = root
            return status

        def stage(label: str, hours: float, word: str) -> int:
            """推进 hours 个矿井小时，等含 word 的告警和投递，再等智能体处理完；返回本段开始前的时间线条数。"""
            say(f"{label}：推进 {hours:g} 矿井小时，等监听器告警“{word}”")
            iid = ctx.get("iid")
            before = count(store, iid) if iid else 0
            n = delivered(store, iid) if iid else 0
            advance(hours)
            ctx["iid"] = iid = wait_for(lambda: has_alert(store, word), 300, f"告警“{word}”")
            say(f"  事件 {iid}")
            wait_for(lambda: delivered(store, iid) > n, 180, "告警被投递")
            if "sid" not in ctx:
                ctx["sid"] = wait_for(lambda: duty_session(incident_dir), 120, "值班会话")
                open_duty_session(page)
            settle()
            return before

        # ── 黄色 ──
        b = stage("步骤 2 黄色", 35.5, "黄色")
        iid = ctx["iid"]
        check("黄色：研判记入时间线", notes(store, iid, b, "assessment") >= 1)
        check("黄色：加密监测已下发（不弹确认），没有其他对外动作", set(actions(store, iid, b)) == {("start_monitoring", "issued")})
        check("黄色：登记了跟踪计划", followups_since(store, iid, b) >= 1)

        # ── 橙色 ──
        b = stage("步骤 3 橙色", 2.0, "橙色")
        acts = actions(store, iid, b)
        check("橙色：防灭火方案经确认下发", ("issue_firefighting_plan", "issued") in acts)
        check("橙色：预警经确认下发", ("publish_warning", "issued") in acts)
        check("橙色：没有封闭方案或启封", not any(t in ("issue_sealing_plan", "approve_unsealing") for t, _ in acts))

        # ── 红色 ──
        say("步骤 4 红色：处置后继续推进矿井时间，等预警升到红色；48 矿井小时内没有升到红色（处置有效）才由操作员注入漏风突增（顶板垮落）")
        b = None
        n = delivered(store, iid)
        for i in range(24):
            if has_alert(store, "红色"):
                break
            if i == 8:
                say("  处置有效，没有自己升到红色：注入漏风突增（演示操作员动作，记录在模拟矿井的处置日志里，source=story）")
                runner.sim("/interventions", {"type": "leak_change", "params": {"factor": 3}, "source": "story"})
            b = count(store, iid)
            advance(6)
            time.sleep(15)
        wait_for(lambda: has_alert(store, "红色"), 120, "告警“红色”")
        b = b if b is not None else count(store, iid)
        wait_for(lambda: delivered(store, iid) > n, 180, "红色告警投递")
        settle()
        acts = actions(store, iid, b)
        check("红色：预警或升级经确认下发", any(t in ("publish_warning", "escalate_emergency") and s == "issued" for t, s in acts))
        sealed = ("issue_sealing_plan", "issued") in acts
        drafted = notes(store, iid, b, "sealing_plan") >= 1  # 红色阶段智能体自己起草了封闭方案（分级表）
        if not sealed:
            say(f"  智能体{'起草了封闭方案但没有走到下发' if drafted else '没有起草封闭方案'}，操作员下达封闭决定")
            ctx["operator_seal"] = True
            tell(page, SEAL_PROMPT.format(panel=panel()))
            settle()
            acts = actions(store, iid, b)
            sealed = ("issue_sealing_plan", "issued") in acts
        say(f"  观察（不计入通过）：红色阶段智能体{'' if drafted else '没有'}自己起草封闭方案{'，下发靠操作员下令' if ctx.get('operator_seal') else '，自己走到了下发'}")
        check("封闭方案经合规审核、确认后下发", sealed)

        # ── 封闭后 ──
        if not store.get(iid)["followups"] or all(f["state"] != "pending" for f in store.get(iid)["followups"]):
            tell(page, "请为这次处置登记复评（跟踪计划）。")
            settle()
        pending = [f for f in store.get(iid)["followups"] if f["state"] == "pending"]
        b = count(store, iid)
        if pending:
            say("步骤 5 封闭后：推进到跟踪计划到点，监听器唤醒智能体复评")
            now = advance(0)
            gap = hours_between(now, max(f["due_at"] for f in pending))
            n = delivered(store, iid)
            advance(max(gap, 0) + 0.1)
            wait_for(lambda: any(f["state"] in ("due", "done") for f in store.get(iid)["followups"]), 180, "followup_due")
            wait_for(lambda: delivered(store, iid) > n, 180, "跟踪到点告警被投递")
            settle()
        check("封闭后：跟踪到点复评记入时间线", notes(store, iid, b, "effect_review") >= 1)

        say(f"步骤 6 启封：推进到封闭后约 {SEALED_HOURS} 矿井小时")
        b6 = count(store, iid)  # 本步开始前的时间线条数：推进期间智能体可能已自己评估启封，不能只看提问之后的条目
        sealed_at = next(e["at"] for e in store.entries(iid) if e["kind"] == "note" and (e.get("data") or {}).get("tool") == "issue_sealing_plan" and (e["data"].get("status") == "issued"))
        now = advance(0)
        advance(max(SEALED_HOURS - hours_between(sealed_at, now), 0))
        time.sleep(30)
        settle()  # 推进期间监听器可能又唤醒了智能体（跟踪到点、复评），先让它处理完
        # 评估不具备条件时（说明哪一项不满足）：不得下发；再推进 48 小时重新评估，最多 6 轮
        prompt, ready, refused_ok, assessed = UNSEAL_PROMPT.format(panel=panel()), False, True, False
        for round_ in range(1, 7):
            b = b6 if round_ == 1 else count(store, iid)
            tell(page, prompt)
            settle()
            acts = actions(store, iid, b)
            texts = [str((e.get("data") or {}).get("text", "")) for e in store.entries(iid)[b:] if e["kind"] == "note" and (e.get("data") or {}).get("type") == "unsealing"]
            say(f"  第 {round_} 轮启封评估：{texts[-1] if texts else '（没有评估记录）'}")
            assessed = assessed or bool(texts)
            ready = bool(texts) and "不具备" not in texts[-1] and "具备启封条件" in texts[-1]
            if not ready and ("approve_unsealing", "issued") in acts:
                refused_ok = False
            if ready or not texts:
                break
            advance(48)
            time.sleep(20)
            prompt = f"又过了 48 小时。请重新评估 {panel()} 采空区火区是否具备启封条件；具备的话提交启封方案，等我确认。"
        check("启封：评估记入时间线", assessed)
        check("启封：条件不满足时没有下发启封", refused_ok)
        check("启封：具备条件后启封方案经确认下发", ready and ("approve_unsealing", "issued") in acts)

        # ── 复盘 ──
        say("步骤 7 复盘：review_timeline")
        tell(page, f"请复盘事件 {iid}，总结从发现异常到现在的处置过程。")
        settle()
        check("复盘调用 review_timeline", "review_timeline" in [n for n, _ in ctx["root"].calls()])

        # ── 收尾 ──
        say("步骤 8 收尾：操作员确认处置结束，智能体关闭事件（有未完成的跟踪计划或未确认告警时先处理）")
        tell(page, f"复盘看完了，事件 {iid} 的处置已经结束，没有其他事项。请收尾：完成还没做完的跟踪计划，确认没有未确认的告警后关闭事件。")
        settle()
        check("收尾：事件关闭", store.get(iid)["status"] == "closed")
        browser.close()

    print("\n== 故事线结果")
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"事件 {iid}：{store.get(iid)['status_name']}；条目 {store.get(iid)['entry_count']} 条（{store.dir}）")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
