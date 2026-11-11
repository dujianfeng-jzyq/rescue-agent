"""guard：校验、经 rescue-mine 执行、台账。

第三版 P2：指令引用记录（record_id），校验通过后交给矿井执行（send_notice / dispatch_order / submit_treatment），
矿井回执写进台账；第一版的研判快照、防灭火方案和启封方案已删除。
"""

import asyncio
import json

from mcp.client import Client
from rescue_contract.client import MineData

EXPECTED_TOOLS = {
    "start_monitoring",
    "publish_warning",
    "escalate_emergency",
    "notify_evacuation",
    "issue_dispatch_order",
    "issue_sealing_plan",
    "issue_firefighting_plan",
    "approve_unsealing",
    "precheck_plan",
    "query_last_issued",
}


def warning_args(rid, **kw):
    return {"notice": {"location": "1201 工作面采空区", "level": "red", "summary": "红色预警", "record_id": rid, **kw}}


# ── 工具集合 ──


def test_tool_set(world):
    async def go():
        async with Client(world.server) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert asyncio.run(go()) == EXPECTED_TOOLS  # 防灭火方案、启封审批都按记录重做（issue_firefighting_plan、approve_unsealing）


# ── 封闭方案：第二道、第三道防线 ──


def test_sealing_plan_is_issued_and_executed_by_the_mine(world):
    rid = world.plan_records()
    r = world.call("issue_sealing_plan", {"record_id": rid})
    assert not r.is_error, r.content[0].text
    out = r.structured_content
    assert out["status"] == "issued" and "确认人" in (out["note"] or "")
    # 每个步骤一条矿井回执，顺序与方案一致；矿井不能执行的步骤会在 mine_not_executed 里如实列出（模拟矿井现在全部能执行）
    assert [m["kind"] for m in out["mine_receipts"]] == ["treatment"] * 8
    assert [m["status"] for m in out["mine_receipts"]] == ["completed"] * 8 and out["mine_not_executed"] == []
    assert [iv.type for iv in world.holder.engine.action_log if iv.type != "monitor"] == ["evacuate", "leak_sealing", "inert_injection", "temporary_seal", "permanent_seal", "grouting"]
    # 台账：来源记录、Origin、回执
    row = world.rows()[-1]
    assert row["status"] == "issued" and row["record_ids"] == [rid] and row["origin"]["channel"] == "web" and row["incident_id"] is None
    assert len(row["mine_receipts"]) == 8 and row["payload"]["record_id"] == rid


def test_redline_violating_plan_is_stopped_even_if_review_says_pass(world):
    """合规审核记录写“通过”，但方案违反红线（缺注惰）：guard 执行前再校验一次，拦下，矿井什么都没收到。"""
    def break_it(plan):  # 去掉注惰，且爆炸风险未知（未知按不通过处理）：违反第 85 条
        plan["steps"] = [s for s in plan["steps"] if s["type"] != "inert_injection"]
        plan["context"]["explosion_risk"] = "unknown"

    rid = world.plan_records(break_it)
    r = world.call("issue_sealing_plan", {"record_id": rid})
    assert r.is_error and "第85条" in r.content[0].text
    assert world.rows()[-1]["status"] == "rejected" and world.holder.engine.action_log == []


def test_stale_plan_record_is_rejected(world):
    rid = world.plan_records()
    world.holder.engine.clock.advance(3)  # 记录时效 2 小时
    r = world.call("issue_sealing_plan", {"record_id": rid})
    assert r.is_error and "已过时" in r.content[0].text and world.holder.engine.action_log == []


def test_wrong_record_for_sealing_is_rejected(world):
    compose_only = world.put("sealing-plan", "compose", {"result": {}})  # 不是合规审核记录
    r = world.call("issue_sealing_plan", {"record_id": compose_only})
    assert r.is_error and "plan-compliance/review" in r.content[0].text
    assert world.rows()[-1]["status"] == "rejected"


def test_issue_sealing_plan_requires_a_record_id(world):
    r = world.call("issue_sealing_plan", {})
    assert r.is_error  # 参数缺失：不再接受直接提交的方案
    r = world.call("issue_sealing_plan", {"plan": {}})
    assert r.is_error


# ── 防灭火方案（不封闭火区）──


def test_firefighting_plan_is_issued_and_executed_by_the_mine(world_s3):
    rid = world_s3.firefighting_records()
    r = world_s3.call("issue_firefighting_plan", {"record_id": rid})
    assert not r.is_error, r.content[0].text
    out = r.structured_content
    assert out["status"] == "issued" and out["mine_not_executed"] == []
    assert [m["status"] for m in out["mine_receipts"]] == ["completed"] * 5
    assert [iv.type for iv in world_s3.holder.engine.action_log if iv.type != "monitor"] == ["leak_sealing", "inert_injection", "grouting"]
    row = world_s3.rows()[-1]
    assert row["tool"] == "issue_firefighting_plan" and row["status"] == "issued" and row["record_ids"] == [rid]


def test_firefighting_plan_cannot_be_issued_as_a_sealing_plan_and_vice_versa(world_s3, world):
    """方案类型和出处按工具核对：防灭火方案记录不能走封闭方案的工具，封闭方案记录也不能走防灭火方案的工具。"""
    r = world_s3.call("issue_sealing_plan", {"record_id": world_s3.firefighting_records()})
    assert r.is_error and "sealing" in r.content[0].text and world_s3.holder.engine.action_log == []
    r = world.call("issue_firefighting_plan", {"record_id": world.plan_records()})
    assert r.is_error and "firefighting" in r.content[0].text and world.holder.engine.action_log == []


def test_redline_violating_firefighting_plan_is_stopped_even_if_review_says_pass(world_s3):
    """审核记录写“通过”，但方案含用水灭火而条件未确认（第79条）：guard 执行前再校验一次，矿井什么都没收到。"""
    def add_water(plan):
        n = len(plan["steps"])
        plan["steps"].append({"order": n + 1, "type": "water_firefighting", "description": "用水灭火", "roadways": [], "team_size": 6, "standby_team": True, "params": {}})

    r = world_s3.call("issue_firefighting_plan", {"record_id": world_s3.firefighting_records(add_water)})
    assert r.is_error and "第79条" in r.content[0].text
    assert world_s3.rows()[-1]["status"] == "rejected" and world_s3.holder.engine.action_log == []


def test_stale_firefighting_record_is_rejected(world_s3):
    rid = world_s3.firefighting_records()
    world_s3.holder.engine.clock.advance(3)
    r = world_s3.call("issue_firefighting_plan", {"record_id": rid})
    assert r.is_error and "已过时" in r.content[0].text and world_s3.holder.engine.action_log == []


def test_firefighting_issue_counts_as_the_last_treatment(world_s3):
    """效果复评缺处置开始时间时查最近一次下发：防灭火方案也算。"""
    world_s3.call("issue_firefighting_plan", {"record_id": world_s3.firefighting_records()})
    found = world_s3.call("query_last_issued", {"panel": "1201"}).structured_content
    assert found["found"] is True and found["tool"] == "issue_firefighting_plan"


# ── 预警、升级 ──


def test_publish_warning_is_sent_to_the_mine_as_a_notice(world):
    rid = world.result_record("fire-assessment", "warning", {"level": "red"})
    r = world.call("publish_warning", warning_args(rid))
    assert not r.is_error, r.content[0].text
    assert r.structured_content["status"] == "issued" and r.structured_content["mine_receipts"][0]["kind"] == "notice"
    (notice,) = world.holder.notices
    assert notice["kind"] == "warning" and notice["level"] == "red" and notice["panel"] == "1201" and "1201 工作面采空区" in notice["message"]


def test_start_monitoring_is_sent_to_the_mine_as_a_monitor_step(world):
    rid = world.result_record("fire-assessment", "warning", {"level": "yellow"})
    r = world.call("start_monitoring", {"record_id": rid})
    assert not r.is_error, r.content[0].text
    assert r.structured_content["status"] == "issued" and r.structured_content["mine_receipts"][0]["kind"] == "treatment"
    assert [iv.type for iv in world.holder.engine.action_log] == ["monitor"]
    row = world.rows()[-1]
    assert row["tool"] == "start_monitoring" and row["record_ids"] == [rid] and row["status"] == "issued"
    # 重复提交：幂等，矿井只执行一次
    world.call("start_monitoring", {"record_id": rid})
    assert [iv.type for iv in world.holder.engine.action_log] == ["monitor"]


def test_start_monitoring_needs_a_fresh_warning_record(world):
    assert world.call("start_monitoring", {"record_id": "RS-20261104T200000-zzzzzz"}).is_error
    wrong = world.result_record("emergency-escalation", "escalation", {"recommend": True})
    r = world.call("start_monitoring", {"record_id": wrong})
    assert r.is_error and "fire-assessment/warning" in r.content[0].text and world.holder.engine.action_log == []
    nolevel = world.result_record("fire-assessment", "warning", {})
    assert world.call("start_monitoring", {"record_id": nolevel}).is_error


def test_warning_level_must_match_the_record(world):
    rid = world.result_record("fire-assessment", "warning", {"level": "red"})
    r = world.call("publish_warning", warning_args(rid, level="yellow"))
    assert r.is_error and "计算结果 红色 不一致" in r.content[0].text
    assert world.holder.notices == [] and world.rows()[-1]["status"] == "rejected"
    r = world.call("publish_warning", warning_args(rid, level="yellow", override_reason="现场判断"))
    assert not r.is_error and "理由：现场判断" in r.structured_content["note"]


def test_warning_needs_a_record(world):
    r = world.call("publish_warning", {"notice": {"location": "1201", "level": "red", "summary": "s"}})
    assert r.is_error  # 第一版的 snapshot_id 已删除，record_id 必填
    r = world.call("publish_warning", warning_args("RS-20261104T200000-zzzzzz"))
    assert r.is_error and "找不到记录" in r.content[0].text


def test_escalation_follows_the_record_conclusion(world):
    yes = world.result_record("emergency-escalation", "escalation", {"recommend": True})
    no = world.result_record("emergency-escalation", "escalation", {"recommend": False})
    esc = lambda rid: {"escalation": {"reason": "红色预警", "recommended_level": "应急响应", "record_id": rid}}  # noqa: E731
    r = world.call("escalate_emergency", esc(no))
    assert r.is_error and "不建议升级" in r.content[0].text and world.holder.notices == []
    r = world.call("escalate_emergency", esc(yes))
    assert not r.is_error and world.holder.notices[0]["kind"] == "info" and world.holder.notices[0]["level"] == "应急响应"


# ── 撤人名单、调度单：按矿井数据核对 ──


def test_evacuation_notice_and_treatment(world):
    people = asyncio.run(MineData(world.mine).personnel())
    working = [p for p in people if p.roadway_id == "R-1201-FACE"][:2]
    assert len(working) == 2
    r = world.call("notify_evacuation", {"notice": {"reason": "补充撤人", "persons": [p.person_id for p in working]}})
    assert not r.is_error, r.content[0].text
    assert [m["kind"] for m in r.structured_content["mine_receipts"]] == ["notice", "treatment"]
    assert world.holder.notices[0]["kind"] == "evacuation" and world.holder.notices[0]["targets"] == [p.person_id for p in working]
    assert [iv.type for iv in world.holder.engine.action_log] == ["evacuate"]
    assert world.holder.engine.action_log[0].roadways == ("R-1201-FACE",)  # 名单里的人所在的巷道


def test_evacuating_an_unknown_person_is_stopped_before_and_after_confirmation(world):
    notice = {"notice": {"reason": "x", "persons": ["NO-SUCH-PERSON"]}}
    # 人工确认之前：预检接口就拦住
    from starlette.testclient import TestClient

    with TestClient(world.server.streamable_http_app()) as http:
        pre = http.post("/redlines/check", json={"tool": "notify_evacuation", "arguments": notice}).json()
    assert not pre["ok"] and "NO-SUCH-PERSON" in pre["problems"][0]
    # 执行时再校验一次
    r = world.call("notify_evacuation", notice)
    assert r.is_error and "NO-SUCH-PERSON" in r.content[0].text and world.holder.notices == []


def _order(qty=5, item="self_rescuer", from_node="N1", to_node="N2"):
    return {"order": {"order_id": "D-1", "purpose": "补充自救器", "lines": [{"item_type": item, "quantity": qty, "unit": "台", "from_node": from_node, "to_node": to_node}]}}


def test_dispatch_is_executed_asynchronously_by_the_mine(world):
    r = world.call("issue_dispatch_order", _order())
    assert not r.is_error, r.content[0].text
    assert r.structured_content["mine_receipts"][0]["status"] == "executing"  # 矿井异步生效，回执用 get_action_status 跟踪
    assert len(world.holder.dispatches) == 1


def test_dispatch_is_checked_against_the_mine_inventory_and_network(world):
    from starlette.testclient import TestClient

    cases = [
        (_order(qty=1000), "超过台账库存"),
        (_order(item="no_such_item"), "不在应急物资台账"),
        (_order(to_node="N999"), "N999"),
    ]
    with TestClient(world.server.streamable_http_app()) as http:
        for args, expect in cases:
            pre = http.post("/redlines/check", json={"tool": "issue_dispatch_order", "arguments": args}).json()
            assert not pre["ok"] and expect in "".join(pre["problems"]), (args, pre)
            r = world.call("issue_dispatch_order", args)
            assert r.is_error and expect in r.content[0].text
    assert world.holder.dispatches == []


# ── 矿井执行失败、幂等 ──


def test_mine_failure_mid_plan_is_reported_with_progress(world, monkeypatch):
    rid = world.plan_records()
    real = MineData.submit_treatment
    calls = []

    async def flaky(self, request):
        calls.append(request.type.value)
        if len(calls) == 4:
            raise OSError("矿井接口断开")
        return await real(self, request)

    monkeypatch.setattr(MineData, "submit_treatment", flaky)
    r = world.call("issue_sealing_plan", {"record_id": rid})
    assert r.is_error and "已执行 3/8 项" in r.content[0].text and "矿井接口断开" in r.content[0].text
    row = world.rows()[-1]
    assert row["status"] == "failed" and len(row["mine_receipts"]) == 3 and row["record_ids"] == [rid]
    # 失败的不算“已下发”：复评取处置开始时间的查询看不到它
    assert world.call("query_last_issued", {"panel": "1201"}).structured_content == {"found": False, "panel": "1201"}


def test_mine_unreachable_for_a_notice_is_a_failure_not_an_issue(world, monkeypatch):
    async def down(self, request):
        raise OSError("矿井接口不通")

    monkeypatch.setattr(MineData, "send_notice", down)
    rid = world.result_record("fire-assessment", "warning", {"level": "red"})
    r = world.call("publish_warning", warning_args(rid))
    assert r.is_error and "未全部下发" in r.content[0].text and world.rows()[-1]["status"] == "failed"


def test_resubmitting_the_same_command_executes_once_on_the_mine(world):
    rid = world.result_record("fire-assessment", "warning", {"level": "red"})
    r1 = world.call("publish_warning", warning_args(rid))
    r2 = world.call("publish_warning", warning_args(rid))
    assert not r1.is_error and not r2.is_error
    assert len(world.holder.notices) == 1  # 幂等键相同，矿井只发一次
    assert r1.structured_content["mine_receipts"][0]["action_id"] == r2.structured_content["mine_receipts"][0]["action_id"]
    assert len([x for x in world.rows() if x["status"] == "issued"]) == 2  # 台账两条：两次都经过了校验和批准


def test_plan_resubmission_does_not_repeat_treatments(world):
    rid = world.plan_records()
    world.call("issue_sealing_plan", {"record_id": rid})
    world.call("issue_sealing_plan", {"record_id": rid})
    assert [iv.type for iv in world.holder.engine.action_log].count("inert_injection") == 1


# ── 矿井时钟 ──


def test_publish_rejected_when_mine_clock_unavailable(world, monkeypatch):
    async def broken(self):
        raise OSError("mine_data 不可用")

    monkeypatch.setattr(MineData, "now", broken)
    rid = world.result_record("fire-assessment", "warning", {"level": "red"})
    r = world.call("publish_warning", warning_args(rid))
    assert r.is_error and "矿井时间" in r.content[0].text
    assert world.rows()[-1]["status"] == "rejected" and world.holder.notices == []


def test_precheck_route_without_mine_clock_blocks_only_timed_tools(world, monkeypatch):
    """时钟故障时：预警、方案、升级拦下（不能跳过时效核对）；撤人名单、调度单照常进入人工确认（它们的核对用矿井数据，取不到会单独报）。"""
    from starlette.testclient import TestClient

    async def broken(self):
        raise OSError("mine_data 不可用")

    monkeypatch.setattr(MineData, "now", broken)
    with TestClient(world.server.streamable_http_app()) as http:
        warn = http.post("/redlines/check", json={"tool": "publish_warning", "arguments": {"notice": {}}}).json()
        plan = http.post("/redlines/check", json={"tool": "issue_sealing_plan", "arguments": {"record_id": "x"}}).json()
    assert not warn["ok"] and "矿井时间" in warn["problems"][0]
    assert not plan["ok"] and "矿井时间" in plan["problems"][0]


# ── 只读工具 ──


def test_precheck_plan_is_read_only_and_lists_rules(world, sample_plan):
    plan = sample_plan
    plan["steps"] = [s for s in plan["steps"] if s["type"] != "inert_injection"]
    plan["context"]["explosion_risk"] = "unknown"
    out = world.call("precheck_plan", {"tool": "issue_sealing_plan", "plan": plan}).structured_content
    assert out["ok"] is False and any("第85条" in p for p in out["problems"])
    assert out["rules_checked_count"] == len(out["rules_checked"]) and "第85条" in out["rule_articles"]
    assert not world.ledger.exists() or world.ledger.read_text() == ""  # 自查不写台账
    assert world.holder.engine.action_log == []


def test_precheck_plan_passes_a_compliant_plan(world, sample_plan):
    out = world.call("precheck_plan", {"tool": "issue_sealing_plan", "plan": sample_plan}).structured_content
    assert out["ok"] is True and out["problems"] == []


def test_query_last_issued_only_sees_issued_plans(world):
    assert world.call("query_last_issued", {"panel": "1201"}).structured_content == {"found": False, "panel": "1201"}
    world.ledger.write_text(
        json.dumps({"tool": "issue_sealing_plan", "status": "rejected", "issued_at": "2026-11-04T19:00:00", "payload": {"panel": "1201"}}) + "\n"
        + json.dumps({"tool": "issue_sealing_plan", "status": "issued", "action_id": "A-1", "issued_at": "2026-11-04T20:00:00", "payload": {"panel": "1201", "plan_id": "SP-1", "title": "t"}}) + "\n"
        + json.dumps({"tool": "issue_sealing_plan", "status": "failed", "issued_at": "2026-11-04T21:00:00", "payload": {"panel": "1201"}}) + "\n"
        + json.dumps({"tool": "publish_warning", "status": "issued", "issued_at": "2026-11-04T22:00:00", "payload": {"location": "1201"}}) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    out = world.call("query_last_issued", {"panel": "1201"}).structured_content
    assert out["found"] and out["issued_at"] == "2026-11-04T20:00:00" and out["plan_id"] == "SP-1"
    assert world.call("query_last_issued", {"panel": "1202"}).structured_content["found"] is False



def test_query_last_issued_falls_back_to_monitoring(world):
    """黄色阶段只有加密监测：复评起算时间取加密监测的开始时间；有方案时方案优先（之后再开的加密监测不挪起算时间）。"""
    rid = world.result_record("fire-assessment", "warning", {"level": "yellow"})
    assert not world.call("start_monitoring", {"record_id": rid}).is_error
    out = world.call("query_last_issued", {"panel": "1201"}).structured_content
    assert out["found"] and out["tool"] == "start_monitoring" and out["issued_at"] == world.rows()[-1]["issued_at"]
    assert world.call("query_last_issued", {"panel": "1202"}).structured_content["found"] is False
    with world.ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"tool": "issue_firefighting_plan", "status": "issued", "action_id": "A-2", "issued_at": "2000-01-01T00:00:00", "payload": {"panel": "1201", "plan_id": "FP-1"}}) + "\n")
    out = world.call("query_last_issued", {"panel": "1201"}).structured_content
    assert out["tool"] == "issue_firefighting_plan" and out["plan_id"] == "FP-1"

# ── 动作分级：guard 的工具与分级表一致 ──


def test_every_guard_tool_is_in_the_action_policy(world):
    """每个工具都登记了分级、登记的工具都存在；C 级是对外动作，A 级是只读（不写台账、不碰矿井）。"""
    from rescue_contract.action_policy import POLICIES, ActionLevel

    assert {p.tool for p in POLICIES} == EXPECTED_TOOLS
    assert {p.tool for p in POLICIES if p.level == ActionLevel.A} == {"precheck_plan", "query_last_issued"}


def test_read_only_tools_do_not_touch_the_mine_or_the_ledger(world, sample_plan):
    world.call("query_last_issued", {"panel": "1201"})
    world.call("precheck_plan", {"tool": "issue_sealing_plan", "plan": sample_plan})
    assert world.rows() == [] and world.holder.engine.action_log == [] and world.holder.notices == [] and world.holder.dispatches == []


# ── 事件时间线（第三版 P4）──


def test_actions_are_noted_on_the_open_incident(tmp_path, monkeypatch, request):
    from rescue_contract.incidents import IncidentStore

    monkeypatch.setenv("RESCUE_INCIDENT_DIR", str(tmp_path / "inc"))
    store = IncidentStore()
    other = store.open("2026-11-05T08:00:00", category="fire", panel="1202", summary="别的工作面")
    iid = store.open("2026-11-05T08:00:00", category="fire", panel="1201", summary="x")
    world = request.getfixturevalue("world")  # 环境变量设好之后才创建 guard
    rid = world.result_record("fire-assessment", "warning", {"level": "red"})
    r = world.call("publish_warning", warning_args(rid, level="yellow"))  # 与记录不一致：被拒
    assert r.is_error
    r = world.call("publish_warning", warning_args(rid))
    assert not r.is_error
    notes = [e for e in store.entries(iid) if e["kind"] == "note"]
    assert [(n["by"], n["data"]["type"], n["data"]["status"]) for n in notes] == [("guard", "action", "rejected"), ("guard", "action", "issued")]
    assert notes[1]["data"]["action_id"] == r.structured_content["action_id"] and notes[1]["data"]["record_ids"] == [rid]
    # 依据的数据时间（引用记录的 as_of）：唤醒插件按它判断告警是否已被这次动作覆盖
    assert notes[1]["data"]["as_of"] == world.records.get(rid).as_of.isoformat()[:19]
    assert [row["incident_id"] for row in world.rows()] == [iid, iid]
    assert [e for e in store.entries(other) if e["kind"] == "note"] == []
    # 没有未关闭事件时：台账 incident_id 为空，指令照常
    store.append(iid, "2026-11-05T09:00:00", "status", "agent", {"status": "closed"})
    assert not world.call("publish_warning", warning_args(rid)).is_error
    assert world.rows()[-1]["incident_id"] is None


def test_evacuation_with_personnel_record_is_noted_on_the_incident(tmp_path, monkeypatch, request):
    """撤人名单本身不引用记录；带上人员记录（record_ids.personnel）时挂到该工作面的事件和确认弹窗上。读不到记录也不拦撤人。"""
    from rescue_contract.incidents import IncidentStore
    from starlette.testclient import TestClient

    monkeypatch.setenv("RESCUE_INCIDENT_DIR", str(tmp_path / "inc"))
    store = IncidentStore()
    iid = store.open("2026-11-05T08:00:00", category="fire", panel="1201", summary="x")
    world = request.getfixturevalue("world")
    rid = world.result_record("fire-assessment", "personnel", {"computed": True, "total_threatened": 2})
    people = asyncio.run(MineData(world.mine).personnel())
    persons = [p.person_id for p in people if p.roadway_id == "R-1201-FACE"][:2]
    notice = {"notice": {"reason": "补充撤人", "persons": persons, "record_id": rid}}
    with TestClient(world.server.streamable_http_app()) as http:
        pre = http.post("/redlines/check", json={"tool": "notify_evacuation", "arguments": notice}).json()
    assert pre["ok"] and pre["summary"]["record_ids"] == [rid] and pre["summary"]["incident_id"] == iid
    assert not world.call("notify_evacuation", notice).is_error
    assert world.rows()[-1]["incident_id"] == iid
    assert [e["data"]["tool"] for e in store.entries(iid) if e["kind"] == "note"] == ["notify_evacuation"]
    others = [p.person_id for p in people if p.roadway_id == "R-1202-IN"][:1]  # 已撤的人不在定位里，换一个
    gone = {"notice": {"reason": "补充撤人", "persons": others, "record_id": "RS-20261105T080000-zzzzzz"}}
    assert not world.call("notify_evacuation", gone).is_error  # 读不到的记录：照常下发，只是挂不上事件
    assert world.rows()[-1]["incident_id"] is None


# ── 启封审批（approve_unsealing，按启封评估记录）──


def _unseal_content(mutate=None, ready=True):
    import json as _json
    from pathlib import Path

    sample = _json.loads((Path(__file__).resolve().parents[3] / "skills/unsealing-plan/tests/cases/unsealing_plan/s5-336h-ready.expected.json").read_text(encoding="utf-8"))
    if mutate:
        mutate(sample["result"]["plan"])
    if not ready:
        sample["result"].update(ready=False, ready_name="不具备启封条件", unmet_labels="封闭时长", plan=None)
    return sample


def test_approve_unsealing_runs_the_fixed_steps_on_the_mine(world_s5):
    rid = world_s5.put("unsealing-plan", "unsealing_plan", _unseal_content())
    r = world_s5.call("approve_unsealing", {"record_id": rid})
    assert not r.is_error, r.content[0].text
    receipt = r.structured_content
    assert receipt["status"] == "issued" and len(receipt["mine_receipts"]) == 5 and receipt["mine_not_executed"] == []
    # 顺序：检查、撤人、断电、启封、观测
    assert [a.type for a in world_s5.holder.engine.action_log] == ["monitor", "evacuate", "power_cutoff", "unseal", "monitor"]
    assert world_s5.rows()[-1]["tool"] == "approve_unsealing" and world_s5.rows()[-1]["record_ids"] == [rid]


def test_approve_unsealing_stops_when_a_prior_step_is_not_executed(world_s5, monkeypatch):
    """矿井不执行断电（真实矿井可能拒绝）：启封不能照常下发（RL-164 要求先检查、断电、撤人）。"""
    from rescue_mine_data_sim import server as sim_server

    monkeypatch.setattr(sim_server, "SIM_TREATMENTS", sim_server.SIM_TREATMENTS - {sim_server.StepType.POWER_CUTOFF})
    rid = world_s5.put("unsealing-plan", "unsealing_plan", _unseal_content())
    r = world_s5.call("approve_unsealing", {"record_id": rid})
    assert r.is_error and "已停止" in r.content[0].text
    assert not any(a.type == "unseal" for a in world_s5.holder.engine.action_log)  # 断电被拒，启封一步都没执行
    row = world_s5.rows()[-1]
    assert row["tool"] == "approve_unsealing" and row["status"] == "failed" and [m["status"] for m in row["mine_receipts"]] == ["completed", "completed", "rejected"]


def test_approve_unsealing_refuses_when_not_ready(world_s5):
    rid = world_s5.put("unsealing-plan", "unsealing_plan", _unseal_content(ready=False))
    r = world_s5.call("approve_unsealing", {"record_id": rid})
    assert r.is_error and "不具备启封条件" in r.content[0].text
    assert world_s5.rows()[-1]["status"] == "rejected" and not any(a.type == "unseal" for a in world_s5.holder.engine.action_log)


def test_approve_unsealing_enforces_rl164(world_s5):
    def drop_power_cutoff(plan):
        plan["steps"] = [s for s in plan["steps"] if s["type"] != "power_cutoff"]

    rid = world_s5.put("unsealing-plan", "unsealing_plan", _unseal_content(drop_power_cutoff))
    r = world_s5.call("approve_unsealing", {"record_id": rid})
    assert r.is_error and "第164条" in r.content[0].text and "切断回风流电源" in r.content[0].text
    assert not any(a.type == "unseal" for a in world_s5.holder.engine.action_log)


def test_approve_unsealing_needs_the_right_record(world_s5):
    wrong = world_s5.result_record("fire-assessment", "warning", {"level": "red"})
    r = world_s5.call("approve_unsealing", {"record_id": wrong})
    assert r.is_error and "unsealing-plan/unsealing_plan" in r.content[0].text
    assert world_s5.call("approve_unsealing", {}).is_error  # 不接受直接提交方案
    assert world_s5.call("approve_unsealing", {"record_id": "RS-20261116T080000-nothere"}).is_error


def test_precheck_summary_for_unsealing(world_s5):
    from rescue_contract.validation import precheck

    rid = world_s5.put("unsealing-plan", "unsealing_plan", _unseal_content())
    out = precheck("approve_unsealing", {"record_id": rid}, world_s5.holder.engine.now(), world_s5.records)
    assert out["ok"], out["problems"]
    names = [c["name"] for c in out["summary"]["checks"]]
    assert "启封条件" in names and "方案类型、时效与红线" in names
    assert out["summary"]["action"].startswith("批准启封 1201 采空区火区启封方案")
    assert out["notes"] and "不可逆" in out["notes"][0]  # 确认人要把关的事项
    bad = precheck("approve_unsealing", {"record_id": rid}, world_s5.holder.engine.now().replace(year=2027), world_s5.records)
    assert not bad["ok"] and any("过时" in p for p in bad["problems"])
