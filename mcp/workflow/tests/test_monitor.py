"""W0 值守、W8 复盘时间线：不经过大模型；事件与基线期望一致。"""

import asyncio
import json
from pathlib import Path

import yaml
from rescue_contract.client import MineData
from rescue_workflow.monitor import Monitor, read_events
from rescue_workflow.server import build_server
from wf_helpers import build_mine, call, sim

EXPECT = yaml.safe_load((Path(__file__).resolve().parents[3] / "eval" / "expectations.yaml").read_text(encoding="utf-8"))
LEVELS = ["none", "blue", "yellow", "orange", "red"]


def run_monitor(holder, store, tmp_path, step_h, horizon_h, apply=()):
    mon = Monitor(MineData(build_mine(holder)), store, tmp_path / "monitor")
    pending = sorted(apply, key=lambda a: a["at_h"])
    t, events = 0.0, []
    while t <= horizon_h + 1e-9:
        if t:
            holder.engine.clock.advance(step_h)
        while pending and pending[0]["at_h"] <= t:
            act = pending.pop(0)
            holder.engine.apply(act["type"], act.get("params"), source="eval")
        events += [{**e, "h": t} for e in asyncio.run(mon.tick(holder.engine.now()))]
        t += step_h
    return events


def test_s3_level_changes_match_baseline(store, tmp_path):
    exp = EXPECT["s3-accelerating"]
    events = run_monitor(sim("s3-accelerating", 0), store, tmp_path, 1, exp["horizon_h"])
    for level, deadline in exp["reach_by_h"].items():
        reached = [e["h"] for e in events if LEVELS.index(e["level"]) >= LEVELS.index(level)]
        assert reached and min(reached) <= deadline, (level, reached[:3])
    assert all(store.get(e["snapshot_id"]).kind == "assessment" for e in events)
    assert read_events(directory=tmp_path / "monitor") == [{k: v for k, v in e.items() if k != "h"} for e in events]


def test_s1_normal_is_quiet(store, tmp_path):
    assert run_monitor(sim("s1-normal", 0), store, tmp_path, 3, 72) == []


def test_s5_reignition_after_early_unsealing(store, tmp_path):
    exp = EXPECT["s5-sealed-fire-area"]
    events = run_monitor(sim("s5-sealed-fire-area", 0), store, tmp_path, exp["step_h"], exp["horizon_h"], exp["apply"])
    alarms = [e for e in events if e["kind"] == "alarm" and "第164条" in e["message"]]
    assert alarms and min(e["h"] for e in alarms) >= exp["reignition_not_before_h"]
    followups = [e for e in events if e["kind"] == "unseal_followup"]
    assert followups and all(240 < e["h"] <= 240 + 72 for e in followups)


def test_timeline_merges_ledger_events_and_snapshots(store, tmp_path, monkeypatch):
    holder = sim("s4-escalation", 55)
    monkeypatch.setenv("RESCUE_MONITOR_DIR", str(tmp_path / "monitor"))
    monkeypatch.setenv("RESCUE_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    asyncio.run(Monitor(MineData(build_mine(holder)), store, tmp_path / "monitor").tick(holder.engine.now()))
    rec = {"action_id": "A-9", "tool": "issue_sealing_plan", "status": "issued", "issued_at": holder.engine.now().isoformat(), "payload": {"plan_id": "SP-1201-1", "panel": "1201"}}
    (tmp_path / "ledger.jsonl").write_text(json.dumps(rec) + "\n")
    (tmp_path / "audit.jsonl").write_text(json.dumps({"ts": "2026-11-02T16:24:15Z", "tool": "mcp__actions__publish_warning", "outcome": "denied", "detail": 'the user rejected tool "x"'}) + "\n")
    out = call(build_server(build_mine(holder), store), "review_timeline", {}).structured_content
    sources = {x["source"] for x in out["result"]["timeline"]}
    assert sources == {"指令台账", "值守", "研判快照"}
    assert "下发封闭方案 SP-1201-1：已发布" in out["report_markdown"]
    assert "需人工核对" in out["report_markdown"]  # 不推断是谁拒绝的


def test_get_monitor_events_tool(store, tmp_path, monkeypatch):
    holder = sim("s4-escalation", 55)
    monkeypatch.setenv("RESCUE_MONITOR_DIR", str(tmp_path / "monitor"))
    asyncio.run(Monitor(MineData(build_mine(holder)), store, tmp_path / "monitor").tick(holder.engine.now()))
    out = call(build_server(build_mine(holder), store), "get_monitor_events", {}).structured_content
    assert out["count"] == len(out["events"]) >= 1
