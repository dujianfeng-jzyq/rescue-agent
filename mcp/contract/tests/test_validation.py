from datetime import datetime, timedelta

from rescue_contract.validation import precheck

NOW = datetime(2026, 11, 2, 10, 0)


def plan(as_of: datetime, kind: str = "sealing", steps=("evacuate", "inert_injection", "temporary_seal")):
    return {
        "plan_id": "P-1",
        "kind": kind,
        "title": "t",
        "context": {"fire_type": "goaf_spontaneous_combustion", "as_of": as_of.isoformat(), "explosion_risk": "explosive"},
        "steps": [{"order": i + 1, "type": t, "description": t} for i, t in enumerate(steps)],
    }


def test_non_plan_tools_pass():
    assert precheck("publish_warning", {}, NOW) == {"ok": True, "problems": []}


def test_valid_plan_passes():
    assert precheck("issue_sealing_plan", {"plan": plan(NOW)}, NOW)["ok"]


def test_bad_format_rejected():
    r = precheck("issue_sealing_plan", {"plan": {"kind": "sealing"}}, NOW)
    assert not r["ok"] and "数据契约" in r["problems"][0]


def test_stale_context_rejected_only_when_now_given():
    old = plan(NOW - timedelta(hours=5))
    assert not precheck("issue_sealing_plan", {"plan": old}, NOW)["ok"]
    assert precheck("issue_sealing_plan", {"plan": old}, None)["ok"]


def test_kind_mismatch_rejected():
    r = precheck("issue_firefighting_plan", {"plan": plan(NOW)}, NOW)
    assert not r["ok"] and "方案类型" in r["problems"][0]
