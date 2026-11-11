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
import re
import subprocess
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


def sim_get(path: str) -> dict:
    req = urllib.request.Request(f"{SIM}{path}", headers={"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})
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
    if case.get("overrides"):
        # 读数设置只作用于设置之后：设置后推进一小段，让读数里有被设置的值（用来构造互相矛盾的测点，只改读数不改物理模型）
        for o in case["overrides"]:
            sim("/overrides", {"sensor_id": o["sensor_id"], "measurand": o.get("measurand"), "mode": o.get("mode", "fixed"), "value": o.get("value")})
        now = sim("/advance", {"hours": float(case.get("override_advance_h", 0.5))})["now"]
    return now, fmt


# ── 网页端操作 ──────────────────────────────────────────────────────────────

MODEL_BUTTON = 'button[aria-label^="Select model"]'


def setup_incident(case: dict, mine_now: str, fmt: dict[str, str]) -> None:
    """用例要求时在事件库（RESCUE_INCIDENT_DIR，make dev-eval 指向 var/eval/incidents）里建一个事件和一条告警，模拟监听器已经发现异常；
    提问模板里的 {incident_id}、{alert_id}、{mine_now} 由这里填。事件库里该工作面已有的未关闭事件先关掉，免得上一次的残留混进来。"""
    spec = case.get("incident")
    from rescue_contract.incidents import IncidentStore

    if not spec:
        # 没有事件的用例也要清掉上一个用例留下的未关闭事件（它们的待确认、跟踪计划会被智能体读到，2026-11-07 污染过 s4-watch-wake-assess）
        os.environ.setdefault("RESCUE_INCIDENT_DIR", str(ROOT / "var" / "eval" / "incidents"))
        store = IncidentStore()
        for v in store.list():
            if v["status"] != "closed":
                store.append(v["incident_id"], mine_now, "status", "eval", {"status": "closed", "reason": "评测开始前清理"})
        return

    # 与 make dev-eval 写的目录一致；缺省的 var/incidents 是演示和复盘用的，运行器不能往里写，
    # 也不能让 DSH 读不到它建的事件（2026-11-06 启动时漏设环境变量，智能体找不到事件）
    os.environ.setdefault("RESCUE_INCIDENT_DIR", str(ROOT / "var" / "eval" / "incidents"))
    store = IncidentStore()
    panel = spec.get("panel", "1201")
    for v in store.list():
        if v["panel"] == panel:
            store.append(v["incident_id"], mine_now, "status", "eval", {"status": "closed", "reason": "评测开始前清理"})
    iid = store.open(mine_now, category=spec.get("category", "fire"), panel=panel, summary=spec["summary"], by="sentinel")
    if spec.get("followup"):
        store.append(iid, mine_now, "followup_added", "agent", {"followup_id": "FU-eval", "due_at": mine_now[:19], "expect": spec["followup"]})
        store.append(iid, mine_now, "followup_due", "sentinel", {"followup_id": "FU-eval"})
    # 事件里已经有的旧告警（已投递、已处理）：重复投递、相似旧记录这类用例用
    for prior in spec.get("prior_alerts", []):
        old = store.append(iid, mine_now, "alert", "sentinel", {"cause": prior["cause"], "summary": prior["summary"]})
        store.append(iid, mine_now, "delivered", "eval", {"alert_id": old["event_id"], "session_id": "eval"})
        store.append(iid, mine_now, "acked", "agent", {"alert_id": old["event_id"]})
        store.append(iid, mine_now, "note", "agent", {"type": "assessment", "text": prior.get("note", f"已处理告警：{prior['summary']}，完成研判并告知操作员"), "as_of": mine_now[:19]})
    if spec.get("pending_approval"):  # 对外动作在等人工确认且已超时升级（告警原因 approval_timeout）
        pa = spec["pending_approval"]
        base = {"approval_key": "eval-c1", "tool": pa["tool"]}
        store.append(iid, mine_now, "note", "approval", {"type": "approval_pending", "confirmer": pa.get("confirmer", ""), "timeout_min": pa.get("timeout_min", 10), "escalate_to": pa.get("escalate_to", ""), **base})
        store.append(iid, mine_now, "note", "sentinel", {"type": "approval_escalated", "escalate_to": pa.get("escalate_to", ""), "waited_min": pa.get("timeout_min", 10), **base})
    alert = store.append(iid, mine_now, "alert", "sentinel", {"cause": spec["cause"], "summary": spec["summary"]})
    if spec.get("handled"):  # 这条告警本身已经处理过（现在是重复投递）
        store.append(iid, mine_now, "delivered", "eval", {"alert_id": alert["event_id"], "session_id": "eval"})
        store.append(iid, mine_now, "acked", "agent", {"alert_id": alert["event_id"]})
        store.append(iid, mine_now, "note", "agent", {"type": "assessment", "text": f"已处理告警 {alert['event_id']}：{spec['summary']}，完成研判并告知操作员", "as_of": mine_now[:19]})
    # 消息由评测运行器代投（提问就是投递的消息）：立刻标为已投递，免得唤醒插件再往值班会话投一份
    store.append(iid, mine_now, "delivered", "eval", {"alert_id": alert["event_id"], "session_id": "eval"})
    fmt.update(incident_id=iid, alert_id=alert["event_id"], mine_now=mine_now[:19])


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


STALE_CHILD_S = 600


def _stale(session: grade.Session) -> bool:
    """最后一个事件距今超过 STALE_CHILD_S 秒（事件时间是毫秒时间戳）。"""
    last = max((e.get("time", 0) for e in session.events), default=0)
    return time.time() - last / 1000 > STALE_CHILD_S


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
        # 值班会话是长期会话：DSH 重启或模型调用丢失会留下“开了回合、没有结束”的子会话（孤儿）。
        # 它们永远不会结束，不能让它们挡住后面每一次等待；超过 STALE_CHILD_S 没有新事件的未结束子会话不计入。
        sessions = [s for s in root.walk() if s is root or s.turns_settled() or not _stale(s)]
        count = sum(len(s.events) for s in sessions)
        if count != last_count:
            last_count, last_change = count, time.time()
        waiting = [a for a in root.approvals() if a["outcome"] is None]
        if waiting:
            # 按钮名随网页端语言：英文 Allow once / Reject，中文 允许一次 / 拒绝（Docker 演示环境是中文）。精确匹配，
            # 否则会点到“Error: the user rejected tool …”那一行（第二次确认时）
            name = re.compile(r"^(Allow once|允许一次)$") if approvals.get(waiting[0]["tool"]) == "allow" else re.compile(r"^(Reject|拒绝)$")
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
    try:  # 超时：留下页面截图和按钮，便于查是不是卡在确认弹窗上
        shot = ROOT / "var" / "eval" / f"timeout-{root_id[-8:]}.png"
        page.screenshot(path=str(shot))
        print(f"  超时，页面截图 {shot}；按钮 {[b.inner_text() for b in page.get_by_role('button').all()][:30]}", flush=True)
    except Exception:  # noqa: BLE001
        pass
    return grade.load_tree(root_id), "timeout"


def answer_question(page: Page, option: str) -> bool:
    """在网页的提问卡片里选 option 并提交（选择器按 DSH 0.2.1-rc.4 网页端，验证清单 #20）。"""
    try:
        exact = page.get_by_text(option, exact=True)
        # 模型常在选项后加“（推荐）”：找不到完全相同的文字时按前缀匹配
        (exact if exact.count() else page.get_by_text(re.compile("^" + re.escape(option)))).last.click(timeout=10_000)
        for label in ("Submit", "提交", "Send", "Answer"):
            btn = page.get_by_role("button", name=label)
            if btn.count():
                btn.last.click(timeout=10_000)
                return True
    except Exception as exc:  # noqa: BLE001
        print(f"   回答提问失败：{exc}")
    return False


def wait_new_turn(root_id: str, turns: int, timeout_s: float) -> bool:
    """等根会话开始新的一轮（后台任务结束后 tool-jobs 唤醒）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if sum(e["type"] == "turn/start" for e in grade.load_tree(root_id).events) > turns:
            return True
        time.sleep(2)
    return False


def _unanswered(s: grade.Session) -> list[tuple[str, str]]:
    done = {e["data"]["message"].get("toolCallId") for e in s.events if e["type"] == "tool/result"}
    return [(e["data"]["name"], e["data"]["callId"]) for e in s.events if e["type"] == "tool/call" and e["data"]["callId"] not in done]


# ── 主流程 ─────────────────────────────────────────────────────────────────


def run_one(page: Page, case: dict, model: str | None, timeout_s: float, quiet_s: float) -> dict:
    mine_now, fmt = setup_sim(case)
    setup_incident(case, mine_now, fmt)
    if case.get("wake"):
        # 提示词由唤醒插件的生成器按事件库里的真实条目生成（与生产里智能体收到的消息同源），不在用例里手抄
        text = subprocess.run(
            ["node", "tools/wake-message.ts", os.environ["RESCUE_INCIDENT_DIR"], fmt["incident_id"], fmt["alert_id"]],
            cwd=ROOT / "dsh-bundle", capture_output=True, text=True, check=True, timeout=60,
        ).stdout  # fmt: skip
    else:
        text = case["prompt"].format(**fmt)
    prompt = " ".join(text.split("\n"))  # 输入框里换行会提前发送：压成一行
    new_session(page)
    if model:
        select_model(page, model)
    t0 = int(time.time() * 1000)
    send(page, prompt)
    root_id = find_root(prompt, t0 - 5000, 120)
    root, status = wait_settled(root_id, page, timeout_s, quiet_s, case.get("approvals"))
    # 持续检测类用例：第一轮结束后推进模拟时钟，等后台任务结束、唤醒智能体；智能体提问时按 answers 依次回答
    answers = list(case.get("answers", []))
    if status == "settled" and case.get("advance_h"):
        turns = sum(e["type"] == "turn/start" for e in root.events)
        sim("/advance", {"hours": float(case["advance_h"])})
        if not wait_new_turn(root_id, turns, 180):
            status = "no_wake"
        else:
            root, status = wait_settled(root_id, page, timeout_s, quiet_s, case.get("approvals"))
    if status == "asked_user" and case.get("operator_cards"):
        # 第五版：智能体用代码给出的卡片（处置选项、封闭方案现场条件）问操作员：运行器扮演操作员，用故事线那套答题逻辑作答
        # （多题卡片逐题答、多选逐项点、点跳过），答完等智能体继续；最多 4 轮，用例不指望智能体提别的问题
        from story import answer_questions  # noqa: PLC0415  story 反过来 import run，放在函数里避免循环导入

        for _ in range(4):
            if status != "asked_user" or answer_questions(page) == 0:
                break
            time.sleep(5)
            root, status = wait_settled(root_id, page, timeout_s, quiet_s, case.get("approvals"))
    while status == "asked_user" and answers:
        option = answers.pop(0)
        if not answer_question(page, option):
            status = "answer_failed"
            break
        time.sleep(5)
        root, status = wait_settled(root_id, page, timeout_s, quiet_s, case.get("approvals"))
    r = grade.grade(root, case["checks"])
    r.update(case=case["id"], requested_model=model, mine_now=mine_now, seconds=round(time.time() - t0 / 1000))
    if r["llm_errors"]:
        status = "llm_error"
    r["status"] = status
    if status in ("timeout", "llm_error", "no_wake", "answer_failed"):
        r["passed"] = False
    return r


VALID = ("settled", "asked_user", "no_wake")  # 模型调用失败、超时、评测运行器答题失败的运行不计入通过率；没被唤醒算系统失败


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
        page.add_init_script("try { localStorage.setItem('rescue-map:disabled', '1') } catch (e) {}")  # 用 DSH 原界面，不用地图界面
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
