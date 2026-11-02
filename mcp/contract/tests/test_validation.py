from datetime import datetime, timedelta

import pytest
from rescue_contract.models import Snapshot
from rescue_contract.snapshots import SnapshotStore, new_id
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
    assert precheck("publish_warning", {}, NOW) == {"ok": True, "problems": [], "notes": []}


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


# ── 研判快照核对（docs/workflow-design.md W6）──


CTX = {
    "fire_type": "goaf_spontaneous_combustion", "as_of": NOW.isoformat(), "methane_pct": 0.6, "methane_rising": False,
    "o2_pct": None, "area_temperature_c": None, "visibility_m": None, "explosion_risk": "may_become_explosive",
    "fire_source_confirmed": None, "water_supply_sufficient": None, "return_airway_clear": None, "sealed_at": None,
}  # fmt: skip
REQUIRED = ["evacuate", "inert_injection", "temporary_seal", "monitor", "permanent_seal"]


def _store(tmp_path, kind="plan-context", result=None) -> tuple[SnapshotStore, str]:
    store = SnapshotStore(tmp_path)
    sid = new_id(NOW)
    result = result or {"plan": {"kind": "sealing", "context": CTX}, "required_steps": REQUIRED}
    store.put(Snapshot(snapshot_id=sid, kind=kind, panel="1201", as_of=NOW, created_at=NOW, result=result))
    return store, sid


def _plan(sid, steps=REQUIRED, **ctx):
    return {
        "plan_id": "SP-1", "kind": "sealing", "title": "t", "snapshot_id": sid, "panel": "1201",
        "context": {**CTX, **ctx}, "steps": [{"order": i + 1, "type": t, "description": t} for i, t in enumerate(steps)],
    }  # fmt: skip


def test_plan_matching_snapshot_passes(tmp_path):
    store, sid = _store(tmp_path)
    steps = ["evacuate", "inert_injection", "temporary_seal", "grouting", "monitor", "permanent_seal", "monitor"]  # 插入可选步骤可以
    r = precheck("issue_sealing_plan", {"plan": _plan(sid, steps)}, NOW, store)
    assert r["ok"], r


@pytest.mark.parametrize(
    ("change", "expect"),
    [
        ({"snapshot_id": None}, "缺少 snapshot_id"),
        ({"snapshot_id": "SN-20261104T150000-nosuch"}, "找不到快照"),
        ({"ctx": {"explosion_risk": "not_explosive"}}, "explosion_risk"),
        ({"ctx": {"fire_source_confirmed": True}}, "fire_source_confirmed"),
        ({"ctx": {"methane_pct": 0.4}}, "methane_pct"),
        ({"steps": ["evacuate", "temporary_seal", "inert_injection", "monitor", "permanent_seal"]}, "顺序"),
        ({"steps": ["evacuate", "inert_injection", "temporary_seal", "permanent_seal"]}, "必备步骤"),
    ],
)
def test_plan_not_matching_snapshot_is_rejected(tmp_path, change, expect):
    store, sid = _store(tmp_path)
    plan = _plan(change.get("snapshot_id", sid), change.get("steps", REQUIRED), **change.get("ctx", {}))
    r = precheck("issue_sealing_plan", {"plan": plan}, NOW, store)
    assert not r["ok"] and expect in "\n".join(r["problems"]), r


def test_plan_wrong_snapshot_kind_is_rejected(tmp_path):
    store, sid = _store(tmp_path, kind="assessment", result={"warning": {"level": "red"}})
    r = precheck("issue_sealing_plan", {"plan": _plan(sid)}, NOW, store)
    assert not r["ok"] and "类型" in r["problems"][0]


@pytest.mark.parametrize(
    ("level", "reason", "ok", "note"),
    [("red", None, True, False), ("orange", None, False, False), ("orange", "现场已组织撤人，指挥部决定按橙色发布", True, True)],
)
def test_warning_level_checked_against_snapshot(tmp_path, level, reason, ok, note):
    store, sid = _store(tmp_path, kind="assessment", result={"warning": {"level": "red"}})
    notice = {"location": "1201", "level": level, "summary": "s", "snapshot_id": sid, "override_reason": reason}
    r = precheck("publish_warning", {"notice": notice}, NOW, store)
    assert r["ok"] is ok and bool(r["notes"]) is note, r


def test_warning_without_snapshot_is_rejected(tmp_path):
    r = precheck("publish_warning", {"notice": {"location": "1201", "level": "red", "summary": "s"}}, NOW, SnapshotStore(tmp_path))
    assert not r["ok"] and "缺少 snapshot_id" in r["problems"][0]


@pytest.mark.parametrize(("recommend", "ok"), [(True, True), (False, False)])
def test_escalation_needs_recommending_snapshot(tmp_path, recommend, ok):
    store, sid = _store(tmp_path, kind="escalation", result={"recommend": recommend})
    esc = {"reason": "r", "recommended_level": "应急响应", "snapshot_id": sid}
    assert precheck("escalate_emergency", {"escalation": esc}, NOW, store)["ok"] is ok


def test_dispatch_order_lines_checked_when_snapshot_given(tmp_path):
    line = {"item_type": "temporary_seal_kit", "quantity": 2, "unit": "套", "from_node": "N1", "to_node": "N5"}
    store, sid = _store(tmp_path, kind="dispatch", result={"order": {"lines": [line]}})
    ok = {"order_id": "DO-1", "purpose": "p", "lines": [line], "snapshot_id": sid}
    bad = {**ok, "lines": [{**line, "quantity": 3}]}
    assert precheck("issue_dispatch_order", {"order": ok}, NOW, store)["ok"]
    assert not precheck("issue_dispatch_order", {"order": bad}, NOW, store)["ok"]
    assert precheck("issue_dispatch_order", {"order": {**ok, "snapshot_id": None}}, NOW, store)["ok"]


# ── 预警、升级引用的快照也要核对时效 ──


@pytest.mark.parametrize(
    ("tool", "kind", "result", "key", "args"),
    [
        ("publish_warning", "assessment", {"warning": {"level": "red"}}, "notice", {"location": "1201", "level": "red", "summary": "s"}),
        ("escalate_emergency", "escalation", {"recommend": True}, "escalation", {"reason": "r", "recommended_level": "应急响应"}),
    ],
)
@pytest.mark.parametrize(("age_h", "ok"), [(1, True), (3, False), (-1, False)])
def test_warning_and_escalation_snapshot_must_be_fresh(tmp_path, tool, kind, result, key, args, age_h, ok):
    store = SnapshotStore(tmp_path)
    as_of = NOW - timedelta(hours=age_h)
    sid = new_id(as_of)
    store.put(Snapshot(snapshot_id=sid, kind=kind, panel="1201", as_of=as_of, created_at=NOW, result=result))
    r = precheck(tool, {key: {**args, "snapshot_id": sid}}, NOW, store)
    assert r["ok"] is ok, r
    if not ok:
        assert ("过时" if age_h > 0 else "晚于") in r["problems"][0]
    assert precheck(tool, {key: {**args, "snapshot_id": sid}}, None, store)["ok"]  # 自查不核对时效


# ── 人工确认弹窗：逐项列出人工输入和未确认的现场条件 ──


def test_plan_notes_list_human_inputs_and_missing_fields(tmp_path):
    store = SnapshotStore(tmp_path)
    sid = new_id(NOW)
    ctx = {**CTX, "fire_source_confirmed": True, "area_temperature_c": 32.0}
    human = [
        {"field": "fire_source_confirmed", "value": True, "confirmed_by": "救护队带队指挥员", "note": None},
        {"field": "area_temperature_c", "value": 32.0, "confirmed_by": "通风区长", "note": None},
    ]
    result = {"plan": {"kind": "sealing", "context": ctx}, "required_steps": REQUIRED}
    store.put(
        Snapshot(
            snapshot_id=sid, kind="plan-context", panel="1201", as_of=NOW, created_at=NOW, inputs={"human_inputs": human}, result=result
        )
    )
    r = precheck("issue_sealing_plan", {"plan": _plan(sid, **ctx)}, NOW, store)
    assert r["ok"], r
    given, missing = r["notes"]
    assert "火源是否明确=是（确认人：救护队带队指挥员）" in given and "灾区温度（℃）=32.0（确认人：通风区长）" in given
    assert "能见度" in missing and "回风道是否畅通" in missing and "火源" not in missing


def test_plan_notes_without_human_inputs_list_all_missing(tmp_path):
    store, sid = _store(tmp_path)
    r = precheck("issue_sealing_plan", {"plan": _plan(sid)}, NOW, store)
    assert r["ok"] and len(r["notes"]) == 1 and "未经人工确认" in r["notes"][0], r
