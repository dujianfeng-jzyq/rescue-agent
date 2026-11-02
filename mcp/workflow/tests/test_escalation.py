"""W2 效果评估、W3 升级建议，以及升级指令按快照核对。"""

import json

import grade
import pytest
from rescue_actions.server import build_server as actions
from rescue_workflow.server import build_server
from wf_helpers import build_mine, call, sim


def inerted_s2():
    """s2 第 48 小时注氮加回风侧堵漏，快进到第 72 小时（与 eval/agent/cases.yaml 的 s2-effect-after-inerting 相同）。"""
    holder = sim("s2-slow-oxidation", 48)
    since = holder.engine.now()
    holder.engine.apply("inert_injection", {"rate_m3_h": 1000}, source="eval")
    holder.engine.apply("leak_sealing", {"reduction": 0.5}, ["R-1201-RET"], source="eval")
    holder.engine.clock.advance(24)
    return holder, since


def run(holder, store, tool, **args):
    r = call(build_server(build_mine(holder), store), tool, args)
    assert not r.is_error, r.content[0].text
    return r.structured_content


def test_effect_after_inerting_needs_continuing(store):
    holder, since = inerted_s2()
    out = run(holder, store, "review_effect", since=since.isoformat())
    assert out["result"]["overall_name"] == "需继续处置"  # 场景 s2 的设计结论
    assert out["result"]["since_source"] == "指挥人员给出"
    assert store.get(out["snapshot_id"]).kind == "effect"
    by_type = out["result"]["counts_by_type"]
    assert sum(g["total"] for g in by_type) == out["result"]["evaluated"]
    assert {g["type"] for g in by_type} == {"采空区束管", "采空区测温", "安全监控"}
    assert "采空区束管 CO 共" in out["report_markdown"]


def test_effect_since_from_ledger(store, tmp_path):
    holder, since = inerted_s2()
    rec = {
        "action_id": "A-1",
        "tool": "issue_firefighting_plan",
        "status": "issued",
        "issued_at": since.isoformat(),
        "payload": {"plan_id": "FP-1201-1", "panel": "1201"},
    }
    (tmp_path / "ledger.jsonl").write_text(json.dumps(rec) + "\n")
    out = run(holder, store, "review_effect")
    assert out["result"]["since"] == since.isoformat()
    assert "A-1" in out["result"]["since_source"]


def test_effect_without_since_or_ledger_asks_for_time(store):
    r = call(build_server(build_mine(sim("s2-slow-oxidation", 72)), store), "review_effect", {})
    assert r.is_error and "since" in r.content[0].text


@pytest.mark.parametrize(("scenario", "hours", "recommend"), [("s4-escalation", 55, True), ("s2-slow-oxidation", 48, False)])
def test_recommend_escalation(store, scenario, hours, recommend):
    out = run(sim(scenario, hours), store, "recommend_escalation")
    r = out["result"]
    assert r["recommend"] is recommend
    assert {a["article"] for a in r["articles"]} == {"第39条", "第40条", "第73条"}
    assert all(a["content"] for a in r["articles"])
    assert out["report_markdown"].startswith("【预警研判】")  # 先放研判快照的报告，阶段等要点自成完整结论


def test_worsening_effect_triggers_recommendation(store, monkeypatch):
    from rescue_workflow import escalation

    holder = sim("s2-slow-oxidation", 48)
    a = run(holder, store, "assess_panel")["result"]
    assert escalation.recommend(a, None)["recommend"] is False
    assert escalation.recommend(a, {"overall": "worsening", "overall_name": "恶化", "since": "2026-11-04T08:00:00"})["recommend"] is True


@pytest.mark.parametrize(("scenario", "hours", "ok"), [("s4-escalation", 55, True), ("s2-slow-oxidation", 48, False)])
def test_escalate_needs_recommending_snapshot(store, tmp_path, scenario, hours, ok):
    holder = sim(scenario, hours)
    sid = run(holder, store, "recommend_escalation")["snapshot_id"]
    server = actions(build_mine(holder), ledger_path=tmp_path / "ledger.jsonl", sim_control_url="", store=store)
    r = call(server, "escalate_emergency", {"escalation": {"reason": "r", "recommended_level": "应急响应", "snapshot_id": sid}})
    assert r.is_error is not ok, r.content[0].text


@pytest.mark.parametrize("tool", ["review_effect", "recommend_escalation"])
def test_report_numbers_come_from_result(store, tool):
    holder, since = inerted_s2()
    args = {"since": since.isoformat()} if tool == "review_effect" else {}
    out = run(holder, store, tool, **args)
    material = json.dumps(out["result"], ensure_ascii=False) + out["snapshot_id"]
    if tool == "recommend_escalation":
        material += json.dumps(store.get(out["assessment_snapshot_id"]).result, ensure_ascii=False) + out["assessment_snapshot_id"]
    pool = {tok for tok, _ in grade.numbers(material)}
    text = out["report_markdown"].split("\n\n![")[0] + "\n" + out["report_markdown"].split("\n\n【升级建议】")[-1]
    missing = [tok for tok, _ in grade.numbers(text) if grade.match_level(tok, pool) != "exact"]
    assert missing == []
