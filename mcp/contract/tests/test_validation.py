"""方案类型、时效、红线（validate_plan），以及预检对不引用记录的指令的处理。按记录核对见 test_validation_records.py、
test_validation_plan_records.py。"""

from datetime import datetime, timedelta

from rescue_contract.models import Plan
from rescue_contract.validation import precheck, validate_plan

NOW = datetime(2026, 11, 2, 10, 0)


def plan(as_of: datetime, kind: str = "sealing", steps=("evacuate", "inert_injection", "temporary_seal")) -> Plan:
    return Plan.model_validate(
        {
            "plan_id": "P-1",
            "kind": kind,
            "title": "t",
            "context": {"fire_type": "goaf_spontaneous_combustion", "as_of": as_of.isoformat(), "explosion_risk": "explosive"},
            "steps": [{"order": i + 1, "type": t, "description": t} for i, t in enumerate(steps)],
        }
    )


def test_valid_plan_passes():
    assert validate_plan("issue_sealing_plan", plan(NOW), NOW) == []


def test_stale_context_rejected_only_when_now_given():
    old = plan(NOW - timedelta(hours=5))
    assert "已过时" in validate_plan("issue_sealing_plan", old, NOW)[0]
    assert validate_plan("issue_sealing_plan", old, None) == []


def test_future_context_rejected():
    assert "晚于矿井当前时间" in validate_plan("issue_sealing_plan", plan(NOW + timedelta(hours=1)), NOW)[0]


def test_kind_mismatch_rejected():
    r = validate_plan("issue_sealing_plan", plan(NOW, kind="firefighting"), NOW)
    assert "方案类型" in r[0]


def test_redline_violation_rejected():
    r = validate_plan("issue_sealing_plan", plan(NOW, steps=("evacuate", "temporary_seal")), NOW)
    assert any("第85条" in p for p in r)


def test_evacuation_and_dispatch_do_not_need_a_record():
    for tool in ("notify_evacuation", "issue_dispatch_order"):
        r = precheck(tool, {}, NOW, None)
        assert (r["ok"], r["problems"], r["notes"]) == (True, [], []), tool


def test_unknown_tool_is_not_blocked_by_validation():
    """校验只管它认识的指令；未知工具由动作分级表和审批插件处理（未登记按需确认，见 action_policy）。"""
    assert precheck("some_other_tool", {}, NOW, None)["ok"]
