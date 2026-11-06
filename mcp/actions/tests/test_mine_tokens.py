"""guard 经 HTTP 调 rescue-mine：写入类工具要写入令牌；guard 不再持有模拟器的控制令牌（P2）。"""

import asyncio
import inspect
import json
import socket
import threading
import time
from datetime import UTC, datetime

import pytest
import uvicorn
from mcp.client import Client
from rescue_actions import server as guard_module
from rescue_actions.server import build_server as build_guard
from rescue_contract.canonical import content_sha256
from rescue_contract.models import Origin, Record, RecordKind
from rescue_contract.records import RecordStore, new_record_id
from rescue_contract.serving import build_app
from rescue_mine_data_sim.server import SimHolder
from rescue_mine_data_sim.server import build_server as build_mine


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mine_http(monkeypatch):
    monkeypatch.setenv("RESCUE_MCP_MINE_DATA_TOKEN", "read-token")
    monkeypatch.setenv("RESCUE_MCP_MINE_ACT_TOKEN", "act-token")
    monkeypatch.setenv("RESCUE_SIM_CONTROL_TOKEN", "control-token")
    holder = SimHolder("s4-escalation", manual_clock=True)
    holder.engine.clock.advance(60)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(build_app(build_mine(holder), key="mine_data"), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert time.monotonic() < deadline, "测试服务未能启动"
        time.sleep(0.05)
    yield holder, f"http://127.0.0.1:{port}/mcp"
    srv.should_exit = True
    thread.join(timeout=5)


def _warning(tmp_path, holder):
    store = RecordStore(tmp_path / "records")
    content = {"algorithm_version": "x", "params": {}, "warnings": [], "result": {"level": "red"}}
    now = holder.engine.now()
    rec = Record(
        record_id=new_record_id(RecordKind.RESULT, now), kind=RecordKind.RESULT, panel="1201", as_of=now, created_at=datetime.now(UTC),
        origin=Origin(channel="web", trigger="chat"), source={"skill": "fire-assessment", "script": "warning"},
        content=content, content_sha256=content_sha256(content),
    )  # fmt: skip
    return store, store.put(rec).record_id


def _publish(guard, rid):
    async def go():
        async with Client(guard) as c:
            return await c.call_tool("publish_warning", {"notice": {"location": "1201", "level": "red", "summary": "s", "record_id": rid}})

    return asyncio.run(go())


def test_guard_with_the_act_token_reaches_the_mine(mine_http, tmp_path):
    holder, url = mine_http
    store, rid = _warning(tmp_path, holder)
    guard = build_guard(url, ledger_path=tmp_path / "ledger.jsonl", records=store)
    r = _publish(guard, rid)
    assert not r.is_error, r.content[0].text
    assert len(holder.notices) == 1 and r.structured_content["mine_receipts"][0]["kind"] == "notice"


def test_guard_with_only_the_read_token_cannot_act(mine_http, tmp_path, monkeypatch):
    """守卫拿不到写入令牌（比如配置错了）：校验通过也下发不出去，台账记 failed，不会假装成功。"""
    holder, url = mine_http
    store, rid = _warning(tmp_path, holder)
    guard = build_guard(url, ledger_path=tmp_path / "ledger.jsonl", records=store)
    monkeypatch.delenv("RESCUE_MCP_MINE_ACT_TOKEN")  # guard 一侧只剩读令牌；矿井仍要求写入令牌
    r = _publish(guard, rid)
    assert r.is_error and "未全部下发" in r.content[0].text
    assert holder.notices == []
    assert json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[-1])["status"] == "failed"


def test_guard_holds_no_simulator_control_access():
    """模拟器的控制接口（真值、读数设置、倍速）不属于 rescue-mine 接口：guard 的代码里不再引用它的令牌或地址。"""
    src = inspect.getsource(guard_module)
    for forbidden in ("sim_control", "SIM_CONTROL", "/sim/", "interventions"):
        assert forbidden not in src.replace("模拟器的控制接口", ""), forbidden
