"""rescue-mine 接口（第三版 P1）：自描述、报警、设备、通知与指令的回执，以及写入令牌的读写分离。"""

import asyncio
import socket
import threading
import time
from datetime import timedelta

import pytest
import uvicorn
from rescue_contract.client import MineData, MineDataError
from rescue_contract.conformance import check_tools, run_all
from rescue_contract.interface import ALL_TOOLS, FEATURES, REQUIRED_TOOLS, WRITE_TOOLS, tools_for
from rescue_contract.models import (
    ActionKind,
    ActionStatus,
    DispatchLine,
    DispatchRequest,
    EquipmentState,
    NoticeKind,
    NoticeRequest,
    SensorRole,
    StepType,
    TreatmentRequest,
)
from rescue_contract.serving import build_app
from rescue_mine_data_sim.server import DISPATCH_RUN_MIN, SimHolder, build_server


def make(scenario: str = "s2-slow-oxidation", hours: float = 0):
    holder = SimHolder(scenario, manual_clock=True)
    holder.engine.clock.advance(hours)
    return holder, build_server(holder)


def run(coro):
    return asyncio.run(coro)


def rpc_status(url: str, token: str | None, tool: str, arguments: dict) -> tuple[int, str]:
    """直接发一个 tools/call 的 HTTP 请求，返回 (状态码, 响应体)——证明拒绝确实来自令牌检查，而不是别的错误。"""
    import json
    import urllib.error
    import urllib.request

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_conformance_with_actions_passes_on_sim():
    _, server = make(hours=30)
    results = run(run_all(server, with_actions=True))
    assert [r for r in results if r[1]] == [], results
    assert "check_actions" in dict(results)


def test_actions_checks_are_off_by_default():
    _, server = make(hours=30)
    assert "check_actions" not in dict(run(run_all(server)))


def test_interface_table_is_consistent():
    assert {"send_notice", "dispatch_order", "submit_treatment"} == WRITE_TOOLS
    assert REQUIRED_TOOLS < ALL_TOOLS
    assert tools_for(FEATURES) == ALL_TOOLS
    assert tools_for([]) == REQUIRED_TOOLS  # 没有任何可选功能：不要求 get_action_status
    assert "get_action_status" in tools_for(["notice"])


def test_capabilities_and_description():
    _, server = make()
    md = MineData(server)
    caps = run(md.capabilities())
    assert set(caps.tools) == ALL_TOOLS and set(caps.features) == set(FEATURES)
    desc = run(md.describe())
    assert [p.panel_id for p in desc.panels] == ["1201", "1202"]
    assert SensorRole.UPPER_CORNER in desc.sensor_roles
    sensors = {s.id: s for s in run(md.sensors())}
    assert sensors["SM-1201-UC"].role == SensorRole.UPPER_CORNER and sensors["SM-1201-UC"].panel == "1201"
    assert sensors["SM-RET-MAIN"].panel is None


def test_conformance_flags_capabilities_that_lie():
    """声明的功能与实际工具不一致、角色词表对不上，一致性检查要报错。"""
    holder, server = make()

    async def lying_caps():
        # 去掉一个声明了的工具：get_equipment_status 仍声明在 features 里
        server._tool_manager._tools.pop("get_equipment_status", None)  # noqa: SLF001
        await check_tools(server)

    with pytest.raises(AssertionError, match="工具集合与声明的功能不一致|缺少工具"):
        run(lying_caps())


def test_alarms_follow_threshold_crossings():
    holder, server = make("s4-escalation", 0)
    md = MineData(server)
    assert run(md.alarms(hours=24)) == []
    holder.engine.clock.advance(70)
    alarms = run(md.alarms(hours=72))
    assert alarms, "s4 发展到红色后上隅角 CO 应当越限"
    uc = [a for a in alarms if a.sensor_id == "SM-1201-UC"]
    assert uc and uc[0].threshold == 24.0 and uc[0].value > 24.0
    assert all(a.acknowledged is False for a in alarms)
    active = run(md.alarms(hours=72, active_only=True))
    assert active and all(a.status.value == "active" for a in active)


def test_equipment_follows_treatments():
    holder, server = make()
    md = MineData(server)
    state = {e.equipment_id: e.state for e in run(md.equipment())}
    assert state["EQ-N2-1"] == EquipmentState.STANDBY
    holder.engine.clock.advance(30)
    run(md.submit_treatment(TreatmentRequest(panel="1201", type=StepType.INERT_INJECTION, params={"flow_m3_h": 600}, idempotency_key="t1")))
    state = {e.equipment_id: e.state for e in run(md.equipment())}
    assert state["EQ-N2-1"] == EquipmentState.RUNNING and state["EQ-GROUT-1"] == EquipmentState.STANDBY


def test_treatment_acts_on_the_physical_model_and_is_idempotent():
    holder, server = make(hours=30)
    md = MineData(server)
    req = TreatmentRequest(panel="1201", type=StepType.INERT_INJECTION, params={"flow_m3_h": 600}, idempotency_key="same-key")
    r1, r2 = run(md.submit_treatment(req)), run(md.submit_treatment(req))
    assert r1.action_id == r2.action_id and r1.status == ActionStatus.COMPLETED and r1.kind == ActionKind.TREATMENT
    assert len([iv for iv in holder.engine.action_log if iv.type == "inert_injection"]) == 1  # 只执行一次


def test_unsupported_treatment_is_rejected_by_receipt_not_exception():
    _, server = make()
    r = run(MineData(server).submit_treatment(TreatmentRequest(panel="1201", type=StepType.REVERSE_VENTILATION, idempotency_key="m")))
    assert r.status == ActionStatus.REJECTED and "不能执行" in (r.detail or "")


def test_dispatch_is_asynchronous():
    holder, server = make()
    md = MineData(server)
    line = DispatchLine(item_type="self_rescuer", quantity=10, unit="台", from_node="N1", to_node="N2")
    r = run(md.dispatch_order(DispatchRequest(purpose="补充自救器", lines=[line], idempotency_key="d1")))
    assert r.status == ActionStatus.EXECUTING
    assert run(md.action_status(r.action_id)).status == ActionStatus.EXECUTING
    holder.engine.clock.advance((DISPATCH_RUN_MIN + 1) / 60)
    done = run(md.action_status(r.action_id))
    assert done.status == ActionStatus.COMPLETED and done.updated_at - done.received_at == timedelta(minutes=DISPATCH_RUN_MIN)


def test_input_errors_name_the_problem():
    _, server = make()
    md = MineData(server)
    with pytest.raises(MineDataError, match="NO-SUCH-PANEL"):
        run(md.send_notice(NoticeRequest(kind=NoticeKind.INFO, message="x", panel="NO-SUCH-PANEL", idempotency_key="n")))
    with pytest.raises(MineDataError, match="N999"):
        run(md.dispatch_order(DispatchRequest(purpose="x", lines=[DispatchLine(item_type="a", quantity=1, unit="台", from_node="N999", to_node="N1")], idempotency_key="d")))
    with pytest.raises(MineDataError, match="NO-SUCH-ROADWAY"):
        run(md.submit_treatment(TreatmentRequest(panel="1201", type=StepType.GROUTING, roadways=["NO-SUCH-ROADWAY"], idempotency_key="t")))
    with pytest.raises(MineDataError, match="NO-SUCH-ACTION"):
        run(md.action_status("NO-SUCH-ACTION"))


def test_idempotency_key_cannot_cross_kinds():
    _, server = make()
    md = MineData(server)
    run(md.send_notice(NoticeRequest(kind=NoticeKind.INFO, message="x", idempotency_key="k")))
    with pytest.raises(MineDataError, match="另一种动作"):
        run(md.submit_treatment(TreatmentRequest(panel="1201", type=StepType.GROUTING, idempotency_key="k")))


# ── 写入令牌：读写分离 ──


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def split_token_server(monkeypatch):
    monkeypatch.setenv("RESCUE_MCP_MINE_DATA_TOKEN", "read-token")
    monkeypatch.setenv("RESCUE_MCP_MINE_ACT_TOKEN", "act-token")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(build_app(build_server(SimHolder("s2-slow-oxidation")), key="mine_data"), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert time.monotonic() < deadline, "测试服务未能启动"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    srv.should_exit = True
    thread.join(timeout=5)


NOTICE = {"request": {"kind": "info", "message": "x", "idempotency_key": "tok-1"}}


def test_read_token_cannot_call_write_tools(split_token_server):
    url = split_token_server
    # 读令牌：读取工具正常，写入工具 401 并说明需要写入令牌
    assert rpc_status(url, "read-token", "get_capabilities", {})[0] == 200
    code, text = rpc_status(url, "read-token", "send_notice", NOTICE)
    assert code == 401 and "写入令牌" in text
    # 写入令牌：写入正常（同一个请求），读取也正常（guard 要回读回执）
    code, text = rpc_status(url, "act-token", "send_notice", NOTICE)
    assert code == 200 and "MA-" in text
    assert rpc_status(url, "act-token", "get_capabilities", {})[0] == 200
    # 每个写入类工具都受保护（不只 send_notice）
    for tool in sorted(WRITE_TOOLS):
        assert rpc_status(url, "read-token", tool, {"request": {}})[0] == 401, tool


def test_write_tool_name_cannot_be_hidden_in_a_batch(split_token_server):
    """批量 JSON-RPC 里夹带写入调用，同样要写入令牌。"""
    import json
    import urllib.error
    import urllib.request

    calls = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_clock", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "send_notice", "arguments": NOTICE}},
    ]
    req = urllib.request.Request(
        split_token_server,
        data=json.dumps(calls).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": "Bearer read-token"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(req, timeout=10)
    assert info.value.code == 401


def test_client_uses_the_act_token_for_writes(split_token_server, monkeypatch):
    """MineData 的写入方法自动带写入令牌，读取方法带读令牌；全套一致性检查（含写入类）经 HTTP 通过。"""
    md = MineData(split_token_server)
    r = run(md.send_notice(NoticeRequest(kind=NoticeKind.INFO, message="x", idempotency_key="client-1")))
    assert r.status == ActionStatus.COMPLETED
    assert run(md.action_status(r.action_id)).action_id == r.action_id
    results = run(run_all(split_token_server, with_actions=True))
    assert [x for x in results if x[1]] == [], results


def test_no_or_wrong_token_is_rejected_everywhere(split_token_server):
    for token in (None, "wrong"):
        assert rpc_status(split_token_server, token, "get_capabilities", {})[0] == 401
        assert rpc_status(split_token_server, token, "send_notice", NOTICE)[0] == 401
