"""W7 调度单草案：密闭道数由代码按方案巷道数计算；调度单按快照核对。"""

import json

import grade
from rescue_actions.server import build_server as actions
from rescue_workflow.server import build_server
from wf_helpers import build_mine, call, sim


def edited_plan(holder, store):
    r = call(build_server(build_mine(holder), store), "draft_plan", {"kind": "sealing", "leak_m3_min": 320, "target_o2_pct": 5})
    plan = r.structured_content["plan"]
    for s in plan["steps"]:
        if s["type"] in ("temporary_seal", "permanent_seal"):
            s["roadways"] = ["R-1201-IN", "R-1201-RET"]
    plan["steps"].insert(4, {"order": 5, "type": "grouting", "description": "注浆", "roadways": [], "params": {}})
    for i, s in enumerate(plan["steps"]):
        s["order"] = i + 1
    return plan


def test_dispatch_draft_from_plan(store):
    holder = sim("s4-escalation", 60)
    out = call(build_server(build_mine(holder), store), "draft_dispatch_for_plan", {"plan": edited_plan(holder, store)}).structured_content
    r = out["result"]
    assert r["calculations"]["temporary_seal"]["walls"] == 2 and r["calculations"]["permanent_seal"]["walls"] == 2
    items = {ln["item_type"]: ln["quantity"] for ln in out["order"]["lines"]}
    assert items["temporary_seal_kit"] == r["calculations"]["temporary_seal"]["materials"]["temporary_seal_kit"]
    grout = next(row for row in r["supply"] if row["item_type"] == "grout_material_t")
    assert grout["required"] == r["calculations"]["grout"]["dry_material_t"]
    assert ("grout_material_t" in items) is grout["sufficient"]  # 库存不够的不进调度单，列为缺口
    assert out["order"]["snapshot_id"] == out["snapshot_id"]
    assert {s["item_type"] for s in r["shortages"]} >= {"brick_pcs"}  # 台账没有砖，列为缺口
    assert r["nitrogen_capacity"]["required"] > 0


def test_dispatch_order_checked_against_snapshot(store, tmp_path):
    holder = sim("s4-escalation", 60)
    order = call(
        build_server(build_mine(holder), store), "draft_dispatch_for_plan", {"plan": edited_plan(holder, store)}
    ).structured_content["order"]
    server = actions(build_mine(holder), ledger_path=tmp_path / "ledger.jsonl", sim_control_url="", store=store)
    ok = call(server, "issue_dispatch_order", {"order": order})
    assert not ok.is_error, ok.content[0].text
    bad = json.loads(json.dumps(order))
    bad["lines"][0]["quantity"] += 1
    r = call(server, "issue_dispatch_order", {"order": bad})
    assert r.is_error and "不一致" in r.content[0].text


def test_tampered_plan_cannot_be_dispatched(store):
    holder = sim("s4-escalation", 60)
    plan = edited_plan(holder, store)
    plan["context"]["methane_pct"] = 0.1
    r = call(build_server(build_mine(holder), store), "draft_dispatch_for_plan", {"plan": plan})
    assert r.is_error and "methane_pct" in r.content[0].text


def test_seal_steps_without_roadways_are_reported(store):
    holder = sim("s4-escalation", 60)
    plan = call(build_server(build_mine(holder), store), "draft_plan", {"kind": "sealing"}).structured_content["plan"]
    out = call(build_server(build_mine(holder), store), "draft_dispatch_for_plan", {"plan": plan}).structured_content
    assert any("没有填写巷道" in n for n in out["result"]["notes"])


def test_report_numbers_come_from_result(store):
    holder = sim("s4-escalation", 60)
    out = call(build_server(build_mine(holder), store), "draft_dispatch_for_plan", {"plan": edited_plan(holder, store)}).structured_content
    pool = {tok for tok, _ in grade.numbers(json.dumps(out["result"], ensure_ascii=False) + out["snapshot_id"])}
    missing = [tok for tok, _ in grade.numbers(out["report_markdown"]) if grade.match_level(tok, pool) != "exact"]
    assert missing == []
