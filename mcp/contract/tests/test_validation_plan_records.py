"""第二版 P3：封闭方案只提交审核记录编号，actions 从记录取方案核对（docs/design-v2.md 4.8）。每种不一致一条用例。

方案取自技能样例（sealing-plan/compose、plan-compliance/review 的期望输出），记录按证据插件的格式写入。
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rescue_contract.canonical import content_sha256
from rescue_contract.models import Origin, Plan, Record, RecordKind
from rescue_contract.records import RecordStore, new_record_id
from rescue_contract.validation import CONTEXT_MAX_AGE, plan_from_record, precheck

SKILLS = Path(__file__).resolve().parents[3] / "skills"
COMPOSE = json.loads((SKILLS / "sealing-plan/tests/cases/compose/s4-escalation-60h-full.expected.json").read_text(encoding="utf-8"))
REVIEW = json.loads((SKILLS / "plan-compliance/tests/cases/review/pass.expected.json").read_text(encoding="utf-8"))
NOW = datetime.fromisoformat(COMPOSE["result"]["as_of"])
ORIGIN = Origin(channel="web", trigger="flow")


def put(store, skill, script, content, *, inputs=None, as_of=NOW, panel="1201"):
    rec = Record(
        record_id=new_record_id(RecordKind.RESULT, as_of), kind=RecordKind.RESULT, panel=panel, as_of=as_of, created_at=datetime.now(UTC),
        origin=ORIGIN, source={"skill": skill, "script": script, "inputs": inputs or {}}, content=content, content_sha256=content_sha256(content),
    )  # fmt: skip
    return store.put(rec).record_id


@pytest.fixture
def records(tmp_path):
    return RecordStore(tmp_path / "records")


def reviewed(records, *, compose=COMPOSE, review=REVIEW, as_of=NOW, review_skill=("plan-compliance", "review")):
    pid = put(records, "sealing-plan", "compose", compose, as_of=as_of)
    return put(records, *review_skill, review, inputs={"plan": pid, "scope": "RS-20261104T200000-aaaaaa"}, as_of=as_of)


def with_result(content, **changes):
    c = json.loads(json.dumps(content))
    c["result"].update(changes)
    return c


def check(rid, records, now=NOW):
    return precheck("issue_sealing_plan", {"record_id": rid}, now, records)


def test_reviewed_plan_passes(records):
    rid = reviewed(records)
    r = check(rid, records)
    assert r["ok"], r["problems"]
    s = r["summary"]
    assert s["record_ids"] == [rid]
    assert s["action"] == "下发 1201 采空区火区封闭方案（SP-1201-202611042000）"
    assert [c["name"] for c in s["checks"]] == ["引用记录", "记录时效", "合规审核", "方案记录", "方案类型、时效与红线"]
    assert all(c["ok"] for c in s["checks"])
    assert s["human_items"] == REVIEW["result"]["human_items"] and s["human_items"]
    plan, problems, _, _ = plan_from_record("issue_sealing_plan", rid, records, NOW)
    assert not problems and isinstance(plan, Plan) and plan.record_id == rid and plan.kind == "sealing"


def test_review_not_passed(records):
    rid = reviewed(records, review=with_result(REVIEW, verdict="fail", verdict_name="不通过"))
    r = check(rid, records)
    assert not r["ok"] and "结论是不通过" in r["problems"][0]


def test_reference_to_plan_record_not_review(records):
    pid = put(records, "sealing-plan", "compose", COMPOSE)
    r = check(pid, records)
    assert not r["ok"] and "需要 plan-compliance/review 的结果记录" in r["problems"][0]


def test_review_plan_differs_from_plan_record(records):
    tampered = json.loads(json.dumps(REVIEW))
    tampered["result"]["plan"]["context"]["explosion_risk"] = "not_explosive"
    tampered["result"]["plan"]["steps"] = [s for s in tampered["result"]["plan"]["steps"] if s["type"] != "inert_injection"]
    rid = reviewed(records, review=tampered)
    r = check(rid, records)
    assert not r["ok"] and "不一致" in r["problems"][0]


def test_parent_not_compose(records):
    wid = put(records, "fire-assessment", "warning", COMPOSE)
    rid = put(records, "plan-compliance", "review", REVIEW, inputs={"plan": wid})
    r = check(rid, records)
    assert not r["ok"] and "不是 sealing-plan/compose 的结果记录" in r["problems"][0]


def test_parent_missing(records):
    rid = put(records, "plan-compliance", "review", REVIEW, inputs={"plan": "RS-20261104T200000-zzzzzz"})
    r = check(rid, records)
    assert not r["ok"] and "读不出来" in r["problems"][0]


def test_stale(records):
    rid = reviewed(records)
    r = check(rid, records, now=NOW + CONTEXT_MAX_AGE + timedelta(minutes=1))
    assert not r["ok"] and "已过时" in r["problems"][0]


def test_redline_still_enforced(records):
    # 审核记录说通过，但方案本身触碰红线（无注惰就封闭、爆炸危险性未知）：红线照样拦
    bad = json.loads(json.dumps(COMPOSE))
    plan = bad["result"]["plan"]
    plan["context"]["explosion_risk"] = "unknown"
    plan["steps"] = [s for s in plan["steps"] if s["type"] != "inert_injection"]
    rid = reviewed(records, compose=bad, review=with_result(REVIEW, plan=plan))
    r = check(rid, records)
    assert not r["ok"] and "第85条" in r["problems"][0]


def test_plan_tools_need_a_record_id(records):
    """封闭方案不再接受直接提交的方案：没有 record_id 就不通过。"""
    r = precheck("issue_sealing_plan", {"plan": COMPOSE["result"]["plan"]}, NOW, records)
    assert not r["ok"] and "缺少 record_id" in r["problems"][0]


def test_tools_without_a_record_flow_are_not_plan_tools(records):
    """按记录下发只认 issue_sealing_plan、issue_firefighting_plan、approve_unsealing，其余工具不支持。
    启封审批认启封评估记录（unsealing-plan/unsealing_plan），封闭方案的审核记录不能拿来启封，
    也不能拿来下发防灭火方案（方案记录出自 sealing-plan 而不是 firefighting-plan）。"""
    rid = reviewed(records)
    plan, problems, _, _ = plan_from_record("approve_unsealing", rid, records, NOW)
    assert plan is None and "unsealing-plan/unsealing_plan" in problems[0]
    plan, problems, _, _ = plan_from_record("issue_firefighting_plan", rid, records, NOW)
    assert plan is None and "firefighting-plan/compose" in problems[0]
    plan, problems, _, _ = plan_from_record("publish_warning", rid, records, NOW)
    assert plan is None and "不支持按记录下发" in problems[0]


def test_no_record_dir():
    r = precheck("issue_sealing_plan", {"record_id": "RS-20261104T200000-zzzzzz"}, NOW, None)
    assert not r["ok"] and "fail closed" in r["problems"][0]
