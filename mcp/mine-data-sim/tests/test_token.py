"""设置 RESCUE_MCP_TOKEN 时，经 HTTP 的一致性检查要带令牌通过，不带令牌被拒。"""

import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from rescue_contract.conformance import run_all
from rescue_contract.serving import build_app
from rescue_mine_data_sim.server import SimHolder, build_server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def token_server(monkeypatch):
    monkeypatch.setenv("RESCUE_MCP_TOKEN", "test-token")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(build_app(build_server(SimHolder("s2-slow-oxidation"))), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "测试服务未能启动"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def test_conformance_over_http_with_token(token_server):
    results = asyncio.run(run_all(token_server))
    assert [err for _, err in results if err] == []
    assert len(results) > 1  # check_tools 通过后其余检查也跑了


def test_conformance_over_http_without_token_is_rejected(token_server, monkeypatch):
    monkeypatch.delenv("RESCUE_MCP_TOKEN")
    results = asyncio.run(run_all(token_server))
    assert results[0][0] == "check_tools" and results[0][1] is not None
