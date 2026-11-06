"""三道防线（docs/design-v3.md 5.1）：同一批指令，审批插件的真实代码对真实的 guard 预检，再绕过审批直接调 guard。

1. DSH 审批插件（TS，真实代码）：调用前请求 guard 的 /redlines/check，违规直接 deny，服务不可用也 deny；通过才 ask（弹人工确认）。
2. 人工确认：在 DSH 里，这里不能测（评测用例 s4-warning-rejected、s4-sealing-plan 覆盖）。
3. guard 执行前再校验：即使审批插件被关掉、被绕过，违规指令也到不了矿井。

没有 node 或 dsh-bundle 依赖时跳过 TS 部分（CI 的 Python 任务里没有 node）。
"""

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

BUNDLE = Path(__file__).resolve().parents[3] / "dsh-bundle"
needs_node = pytest.mark.skipif(shutil.which("node") is None or not (BUNDLE / "node_modules" / "yaml").exists(), reason="没有 node 或 dsh-bundle 依赖")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def guard_http(world):
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(world.server.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert time.monotonic() < deadline, "测试服务未能启动"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/redlines/check"
    srv.should_exit = True
    thread.join(timeout=5)


def approval(url: str, tool: str, args: dict, tmp_path: Path) -> dict:
    f = tmp_path / f"{tool}.json"
    f.write_text(json.dumps(args), encoding="utf-8")
    out = subprocess.run(["node", "tools/approval-precheck.ts", url, tool, str(f)], cwd=BUNDLE, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def break_plan(plan):  # 去掉注惰，且爆炸风险未知：违反第 85 条（合规审核记录仍写“通过”）
    plan["steps"] = [s for s in plan["steps"] if s["type"] != "inert_injection"]
    plan["context"]["explosion_risk"] = "unknown"


@needs_node
def test_line1_approval_denies_redline_violation_before_any_human_sees_it(world, guard_http, tmp_path):
    rid = world.plan_records(break_plan)
    r = approval(guard_http, "issue_sealing_plan", {"record_id": rid}, tmp_path)
    assert r["gated"] and r["verdict"]["kind"] == "deny" and r["verdict"]["code"] == "RESCUE_RED_LINE"
    assert "第85条" in r["verdict"]["reason"]


@needs_node
def test_line1_approval_asks_a_human_when_everything_checks_out(world, guard_http, tmp_path):
    rid = world.plan_records()
    r = approval(guard_http, "issue_sealing_plan", {"record_id": rid}, tmp_path)
    assert r["verdict"]["kind"] == "ask"
    reason = r["verdict"]["reason"]
    assert "总指挥 + 救护队带队指挥员" in reason and rid in reason and "红线预检已通过" in reason  # 弹窗列出确认人和依据记录


@needs_node
def test_line1_approval_denies_when_guard_is_unreachable(tmp_path):
    r = approval("http://127.0.0.1:9/redlines/check", "publish_warning", {"notice": {}}, tmp_path)
    assert r["verdict"]["kind"] == "deny" and r["verdict"]["code"] == "RESCUE_PRECHECK_UNAVAILABLE"


@needs_node
def test_line1_read_only_tools_are_not_gated_and_unregistered_ones_are(world, guard_http, tmp_path):
    assert approval(guard_http, "precheck_plan", {}, tmp_path) == {"gated": False}
    assert approval(guard_http, "query_last_issued", {}, tmp_path) == {"gated": False}
    assert approval(guard_http, "some_future_action", {}, tmp_path)["gated"] is True  # 未登记：按 C 级


@needs_node
def test_line1_evacuation_of_an_unknown_person_is_denied_before_confirmation(world, guard_http, tmp_path):
    r = approval(guard_http, "notify_evacuation", {"notice": {"reason": "x", "persons": ["NO-SUCH-PERSON"]}}, tmp_path)
    assert r["verdict"]["kind"] == "deny" and "NO-SUCH-PERSON" in r["verdict"]["reason"]


def test_line3_guard_stops_the_same_violation_if_approval_is_bypassed(world):
    """审批被关掉（或模型绕过它直接调 guard）：违规方案仍发不出去，矿井没有收到任何动作，台账记 rejected。"""
    rid = world.plan_records(break_plan)
    r = world.call("issue_sealing_plan", {"record_id": rid})
    assert r.is_error and "第85条" in r.content[0].text
    assert world.holder.engine.action_log == [] and world.holder.notices == [] and world.rows()[-1]["status"] == "rejected"


def test_line3_guard_stops_a_tampered_record_even_though_approval_never_saw_it(world):
    """记录被改过（内容哈希对不上）：预警等级改成红色也发不出去。"""
    rid = world.result_record("fire-assessment", "warning", {"level": "blue"})
    path = world.records.dir / f"{rid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["content"]["result"]["level"] = "red"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = world.call("publish_warning", {"notice": {"location": "1201", "level": "red", "summary": "s", "record_id": rid}})
    assert r.is_error and "content_sha256" in r.content[0].text and world.holder.notices == []


def test_line3_model_cannot_write_to_the_mine_directly(world):
    """智能体没有任何办法直接写矿井：guard 之外，写入类工具要写入令牌，只有 guard 持有（令牌分离见 test_mine_tokens.py 和
    mine-data-sim/tests/test_mine_interface.py）；这里确认 guard 的工具集合里没有“直通矿井”的工具。"""
    import asyncio

    from mcp.client import Client

    async def go():
        async with Client(world.server) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert not {"send_notice", "dispatch_order", "submit_treatment"} & asyncio.run(go())
