import asyncio
import json
from datetime import timedelta

from mcp.client import Client
from rescue_actions.server import build_server
from rescue_contract.models import FireContext, Snapshot
from rescue_contract.snapshots import SnapshotStore, new_id
from rescue_mine_data_sim.server import SimHolder
from rescue_mine_data_sim.server import build_server as build_mine


def setup(tmp_path, scenario="s4-escalation", hours=50):
    holder = SimHolder(scenario, manual_clock=True)
    holder.engine.clock.advance(hours)
    ledger = tmp_path / "ledger.jsonl"
    server = build_server(build_mine(holder), ledger_path=ledger, sim_control_url="")
    return holder, server, ledger


def sealing_plan(now, steps):
    return {
        "plan_id": "P-1",
        "kind": "sealing",
        "title": "1201 采空区火区封闭",
        "context": {
            "fire_type": "goaf_spontaneous_combustion",
            "as_of": now.isoformat(),
            "methane_pct": 0.6,
            "methane_rising": False,
            "explosion_risk": "may_become_explosive",
        },
        "steps": [{"order": i + 1, "type": t, "description": t} for i, t in enumerate(steps)],
    }


def call(server, tool, args):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args)

    return asyncio.run(go())


def test_sealing_without_inerting_is_rejected_and_logged(tmp_path):
    holder, server, ledger = setup(tmp_path)
    plan = sealing_plan(holder.engine.now(), ["evacuate", "temporary_seal", "permanent_seal"])
    r = call(server, "issue_sealing_plan", {"plan": plan})
    assert r.is_error and "第85条" in r.content[0].text
    rec = json.loads(ledger.read_text().splitlines()[-1])
    assert rec["status"] == "rejected"


def test_valid_sealing_plan_is_issued(tmp_path):
    holder = SimHolder("s4-escalation", manual_clock=True)
    holder.engine.clock.advance(50)
    now = holder.engine.now()
    plan = sealing_plan(now, ["evacuate", "inert_injection", "temporary_seal", "permanent_seal"])
    store = SnapshotStore(tmp_path / "snapshots")
    sid = new_id(now)
    plan["snapshot_id"] = sid
    ctx = FireContext.model_validate(plan["context"]).model_dump(mode="json")
    result = {
        "plan": {"kind": "sealing", "context": ctx},
        "required_steps": ["evacuate", "inert_injection", "temporary_seal", "permanent_seal"],
    }
    store.put(Snapshot(snapshot_id=sid, kind="plan-context", panel="1201", as_of=now, created_at=now, result=result))
    ledger = tmp_path / "ledger.jsonl"
    server = build_server(build_mine(holder), ledger_path=ledger, sim_control_url="", store=store)
    r = call(server, "issue_sealing_plan", {"plan": plan})
    assert not r.is_error, r.content
    assert r.structured_content["status"] == "issued"
    assert json.loads(ledger.read_text().splitlines()[-1])["tool"] == "issue_sealing_plan"


def test_stale_context_is_rejected(tmp_path):
    holder, server, _ = setup(tmp_path)
    plan = sealing_plan(holder.engine.now() - timedelta(hours=5), ["evacuate", "inert_injection", "temporary_seal"])
    r = call(server, "issue_sealing_plan", {"plan": plan})
    assert r.is_error and "过时" in r.content[0].text


def test_wrong_plan_kind_for_tool_is_rejected(tmp_path):
    holder, server, _ = setup(tmp_path)
    plan = sealing_plan(holder.engine.now(), ["evacuate", "inert_injection", "temporary_seal"])
    r = call(server, "issue_firefighting_plan", {"plan": plan})
    assert r.is_error and "方案类型" in r.content[0].text


def test_all_actions_tools_are_command_tools(tmp_path):
    """actions 服务放需要确认的指令；只读工具必须以 precheck_ 开头（审批插件按这个前缀放行，不弹确认）。"""
    _, server, _ = setup(tmp_path)

    async def go():
        async with Client(server) as c:
            return {t.name for t in (await c.list_tools()).tools}

    names = asyncio.run(go())
    assert {n for n in names if n.startswith("precheck_")} == {"precheck_plan"}
    assert names - {"precheck_plan"} == {
        "publish_warning",
        "issue_firefighting_plan",
        "escalate_emergency",
        "notify_evacuation",
        "issue_dispatch_order",
        "issue_sealing_plan",
        "approve_unsealing",
    }


def test_publish_rejected_when_mine_clock_unavailable(tmp_path, monkeypatch):
    from rescue_contract.client import MineData

    async def broken(self):
        raise OSError("mine_data 不可用")

    monkeypatch.setattr(MineData, "now", broken)
    _, server, ledger = setup(tmp_path)
    notice = {"location": "1201", "level": "red", "summary": "s", "snapshot_id": "SN-20261102T100000-abcdef"}
    r = call(server, "publish_warning", {"notice": notice})
    assert r.is_error and "矿井时间" in r.content[0].text
    assert json.loads(ledger.read_text().splitlines()[-1])["status"] == "rejected"


def test_precheck_route_without_mine_clock_blocks_only_timed_tools(tmp_path, monkeypatch):
    """时钟故障时：预警、方案、升级拦下（不能跳过时效核对）；撤人名单照常进入人工确认。"""
    from rescue_contract.client import MineData
    from starlette.testclient import TestClient

    async def broken(self):
        raise OSError("mine_data 不可用")

    monkeypatch.setattr(MineData, "now", broken)
    _, server, _ = setup(tmp_path)
    with TestClient(server.streamable_http_app()) as http:
        warn = http.post("/redlines/check", json={"tool": "publish_warning", "arguments": {"notice": {}}}).json()
        evac = http.post("/redlines/check", json={"tool": "notify_evacuation", "arguments": {}}).json()
    assert not warn["ok"] and "矿井时间" in warn["problems"][0]
    assert evac["ok"], evac


def test_precheck_plan_is_read_only_and_lists_rules(tmp_path):
    holder, server, ledger = setup(tmp_path)
    plan = sealing_plan(holder.engine.now(), ["evacuate", "temporary_seal", "monitor", "permanent_seal"])
    r = call(server, "precheck_plan", {"tool": "issue_sealing_plan", "plan": plan})
    out = r.structured_content
    assert out["ok"] is False and any("第85条" in p for p in out["problems"])
    assert out["rules_checked_count"] == len(out["rules_checked"]) and "第85条" in out["rule_articles"]
    assert not ledger.exists() or ledger.read_text() == ""  # 自查不写台账


def _warning_record(tmp_path, holder, level="red", script="warning"):
    from datetime import UTC, datetime

    from rescue_contract.canonical import content_sha256
    from rescue_contract.models import Origin, Record, RecordKind
    from rescue_contract.records import RecordStore, new_record_id

    store = RecordStore(tmp_path / "records")
    content = {"algorithm_version": "x", "params": {}, "warnings": [], "result": {"level": level}}
    now = holder.engine.now()
    rec = Record(
        record_id=new_record_id(RecordKind.RESULT, now), kind=RecordKind.RESULT, panel="1201", as_of=now, created_at=datetime.now(UTC),
        origin=Origin(channel="web", trigger="chat"), source={"skill": "fire-assessment", "script": script},
        content=content, content_sha256=content_sha256(content),
    )  # fmt: skip
    return store, store.put(rec).record_id


def test_publish_warning_by_record(tmp_path):
    holder = SimHolder("s4-escalation", manual_clock=True)
    holder.engine.clock.advance(60)
    records, rid = _warning_record(tmp_path, holder)
    ledger = tmp_path / "ledger.jsonl"
    server = build_server(build_mine(holder), ledger_path=ledger, sim_control_url="", records=records)
    notice = {"location": "1201 工作面采空区", "level": "red", "summary": "红色预警", "record_id": rid}
    r = call(server, "publish_warning", {"notice": notice})
    assert not r.is_error and r.structured_content["status"] == "issued"
    # 等级与记录不同且没写理由：拒绝并记台账
    r = call(server, "publish_warning", {"notice": {**notice, "level": "yellow"}})
    assert r.is_error and "计算结果 红色 不一致" in r.content[0].text
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert [x["status"] for x in rows] == ["issued", "rejected"]
    assert rows[0]["payload"]["record_id"] == rid
