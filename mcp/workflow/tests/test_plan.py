"""W4 方案草案 + W6 发布核对：现场条件由代码生成，手改就被 actions 拒绝。"""

import json

import grade
import pytest
from rescue_actions.server import build_server as actions
from rescue_workflow.server import build_server
from wf_helpers import build_mine, call, sim

TOOL = {"sealing": "issue_sealing_plan", "unsealing": "approve_unsealing", "firefighting": "issue_firefighting_plan"}


def draft(holder, store, **args):
    r = call(build_server(build_mine(holder), store), "draft_plan", args)
    assert not r.is_error, r.content[0].text
    return r.structured_content


def issue(holder, store, tmp_path, kind, plan):
    server = actions(build_mine(holder), ledger_path=tmp_path / "ledger.jsonl", sim_control_url="", store=store)
    return call(server, TOOL[kind], {"plan": plan})


@pytest.mark.parametrize("kind", ["sealing", "firefighting", "unsealing"])
def test_draft_has_required_steps_and_snapshot(store, kind):
    out = draft(sim("s4-escalation", 60), store, kind=kind)
    plan = out["plan"]
    assert plan["snapshot_id"] == out["snapshot_id"]
    assert [s["type"] for s in plan["steps"]] == out["result"]["required_steps"]
    snap = store.get(out["snapshot_id"])
    assert snap.kind == "plan-context" and snap.parent_ids == [out["assessment_snapshot_id"]]
    assert snap.result["plan"]["context"] == plan["context"]


def test_context_comes_from_assessment(store):
    out = draft(sim("s4-escalation", 60), store, kind="sealing")
    a = store.get(out["assessment_snapshot_id"]).result
    ctx = out["plan"]["context"]
    assert ctx["explosion_risk"] == a["explosion"]["overall"]
    assert ctx["as_of"] == a["as_of"]
    for f in ("area_temperature_c", "visibility_m", "fire_source_confirmed", "water_supply_sufficient", "return_airway_clear"):
        assert ctx[f] is None  # 没有人工确认就是缺失
    assert len(out["missing_inputs"]) >= 5


def test_human_inputs_need_confirmer_and_right_type(store):
    holder = sim("s4-escalation", 60)
    out = draft(
        holder, store, kind="sealing", human_inputs=[{"field": "fire_source_confirmed", "value": True, "confirmed_by": "救护队带队指挥员"}]
    )
    assert out["plan"]["context"]["fire_source_confirmed"] is True
    assert "救护队带队指挥员" in out["report_markdown"]
    bad = call(
        build_server(build_mine(holder), store),
        "draft_plan",
        {"kind": "sealing", "human_inputs": [{"field": "visibility_m", "value": True, "confirmed_by": "x"}]},
    )
    assert bad.is_error and "数值" in bad.content[0].text


def test_inert_rate_only_when_inputs_given(store):
    holder = sim("s4-escalation", 60)
    no = draft(holder, store, kind="sealing")
    assert "rate_m3_h" not in no["plan"]["steps"][1]["params"] and any("注氮流量" in m for m in no["missing_inputs"])
    yes = draft(holder, store, kind="sealing", leak_m3_min=320, target_o2_pct=5)
    assert yes["plan"]["steps"][1]["params"]["rate_m3_h"] == yes["result"]["inert_estimate"]["required_n2_m3_h"]


def test_draft_issued_after_agent_edits_editable_parts(store, tmp_path):
    holder = sim("s4-escalation", 60)
    plan = draft(holder, store, kind="sealing")["plan"]
    plan["steps"][2]["roadways"] = ["R-1201-IN", "R-1201-RET"]
    plan["steps"][2]["description"] += "；进风侧密闭距交叉口 15 米（需现场确认）"
    plan["steps"].insert(4, {"order": 5, "type": "grouting", "description": "注浆覆盖遗煤", "roadways": [], "params": {}})
    for i, s in enumerate(plan["steps"]):
        s["order"] = i + 1
    r = issue(holder, store, tmp_path, "sealing", plan)
    assert not r.is_error, r.content[0].text
    assert r.structured_content["status"] == "issued"


@pytest.mark.parametrize(
    ("tamper", "expect"),
    [
        (
            lambda p: p["context"].update(
                explosion_risk="may_become_explosive" if p["context"]["explosion_risk"] != "may_become_explosive" else "not_explosive"
            ),
            "explosion_risk",
        ),
        (lambda p: p["context"].update(fire_source_confirmed=True), "fire_source_confirmed"),
        (lambda p: p["steps"].pop(1), "必备步骤"),
        (lambda p: p.update(snapshot_id=None), "缺少 snapshot_id"),
    ],
)
def test_tampered_plan_is_rejected(store, tmp_path, tamper, expect):
    holder = sim("s4-escalation", 60)
    plan = draft(holder, store, kind="sealing")["plan"]
    tamper(plan)
    r = issue(holder, store, tmp_path, "sealing", plan)
    assert r.is_error and expect in r.content[0].text, r.content[0].text
    assert json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[-1])["status"] == "rejected"


def test_plan_report_numbers_come_from_result(store):
    out = draft(sim("s4-escalation", 60), store, kind="sealing", leak_m3_min=320, target_o2_pct=5)
    pool = {tok for tok, _ in grade.numbers(json.dumps(out["result"], ensure_ascii=False) + out["snapshot_id"])}
    missing = [tok for tok, _ in grade.numbers(out["report_markdown"]) if grade.match_level(tok, pool) != "exact"]
    assert missing == []


def test_missing_human_inputs_block_only_when_redlines_fail(store):
    """封闭方案没有进入灾区步骤：缺人工确认项不影响红线，不阻塞；进入灾区缺温度时 RL-088 不通过才阻塞。"""
    out = draft(sim("s4-escalation", 60), store, kind="sealing")
    assert out["redline_check"]["ok"] and out["blocking_inputs"] == []
    assert "可以继续提交" in out["report_markdown"]
