"""设置 RESCUE_MCP_TOKEN 时，actions 转发模拟处置要带令牌，模拟矿井才会执行。"""

import socket
import threading
import time

import pytest
import uvicorn
from rescue_actions.server import Sim
from rescue_contract.serving import build_app
from rescue_mine_data_sim.server import SimHolder, build_server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def sim_with_token(monkeypatch):
    monkeypatch.setenv("RESCUE_MCP_TOKEN", "test-token")
    holder = SimHolder("s2-slow-oxidation", manual_clock=True)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(build_app(build_server(holder)), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "测试服务未能启动"
        time.sleep(0.05)
    yield holder, f"http://127.0.0.1:{port}/sim"
    server.should_exit = True
    thread.join(timeout=5)


def test_forward_with_token_reaches_sim(sim_with_token):
    holder, base = sim_with_token
    assert Sim(base).apply("inert_injection", {"rate_m3_h": 1000})
    assert [(iv.type, iv.source) for iv in holder.engine.action_log][-1] == ("inert_injection", "action")


def test_forward_without_token_is_rejected(sim_with_token, monkeypatch):
    holder, base = sim_with_token
    before = len(holder.engine.action_log)
    monkeypatch.delenv("RESCUE_MCP_TOKEN")
    assert not Sim(base).apply("inert_injection", {"rate_m3_h": 1000})
    assert len(holder.engine.action_log) == before
