"""第二版：预警、升级按记录核对（docs/design-v2.md 4.8）。每种不一致一条用例。"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from rescue_contract.canonical import content_sha256
from rescue_contract.models import Origin, Record, RecordKind
from rescue_contract.records import RecordStore, new_record_id
from rescue_contract.validation import CONTEXT_MAX_AGE, precheck

NOW = datetime(2026, 11, 4, 20, 0)
ORIGIN = Origin(channel="web", trigger="chat")


def put(store: RecordStore, skill: str, script: str, result: dict, *, as_of=NOW, kind=RecordKind.RESULT, panel="1201") -> str:
    content = {"algorithm_version": "x", "params": {}, "warnings": [], "result": result}
    rec = Record(
        record_id=new_record_id(kind, as_of), kind=kind, panel=panel, as_of=as_of, created_at=datetime.now(UTC), origin=ORIGIN,
        source={"skill": skill, "script": script}, content=content, content_sha256=content_sha256(content),
    )  # fmt: skip
    return store.put(rec).record_id


@pytest.fixture
def records(tmp_path):
    return RecordStore(tmp_path / "records")


def warn(records, level="red", **kw):
    return {"notice": {"location": "1201 工作面采空区", "level": level, "summary": "红色预警", **kw}}


def check(tool, args, records, now=NOW):
    return precheck(tool, args, now, None, records)


def test_warning_matches_record(records):
    rid = put(records, "fire-assessment", "warning", {"level": "red"})
    r = check("publish_warning", warn(records, record_id=rid), records)
    assert r["ok"], r["problems"]
    s = r["summary"]
    assert s["tool"] == "publish_warning" and s["record_ids"] == [rid]
    assert s["action"] == "发布 1201 工作面采空区 红色预警"
    assert [c["name"] for c in s["checks"]] == ["引用记录", "记录时效", "预警等级"] and all(c["ok"] for c in s["checks"])


def test_record_not_found(records):
    r = check("publish_warning", warn(records, record_id="RS-20261104T200000-zzzzzz"), records)
    assert not r["ok"] and "找不到记录" in r["problems"][0]
    assert r["summary"]["checks"][0] == {"name": "引用记录", "ok": False, "detail": r["problems"][0]}


def test_record_tampered(records):
    rid = put(records, "fire-assessment", "warning", {"level": "blue"})
    path = records.dir / f"{rid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["content"]["result"]["level"] = "red"  # 改了内容，哈希没改
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = check("publish_warning", warn(records, record_id=rid), records)
    assert not r["ok"] and "content_sha256" in r["problems"][0]


@pytest.mark.parametrize(
    ("skill", "script", "kind"),
    [("fire-assessment", "stage", RecordKind.RESULT), ("emergency-escalation", "escalation", RecordKind.RESULT), ("fire-assessment", "warning", RecordKind.EVIDENCE)],
)
def test_wrong_record_type(records, skill, script, kind):
    rid = put(records, skill, script, {"level": "red"}, kind=kind)
    r = check("publish_warning", warn(records, record_id=rid), records)
    assert not r["ok"] and "需要 fire-assessment/warning 的结果记录" in r["problems"][0]


def test_record_stale_and_future(records):
    old = put(records, "fire-assessment", "warning", {"level": "red"}, as_of=NOW - CONTEXT_MAX_AGE - timedelta(minutes=1))
    r = check("publish_warning", warn(records, record_id=old), records)
    assert not r["ok"] and "已过时" in r["problems"][0]
    future = put(records, "fire-assessment", "warning", {"level": "red"}, as_of=NOW + timedelta(hours=1))
    r = check("publish_warning", warn(records, record_id=future), records)
    assert not r["ok"] and "晚于矿井当前时间" in r["problems"][0]


def test_panel_mismatch(records):
    rid = put(records, "fire-assessment", "warning", {"level": "red"}, panel="1202")
    r = check("publish_warning", warn(records, record_id=rid), records)
    assert not r["ok"] and "工作面 1202 不符" in r["problems"][0]


def test_level_differs_without_and_with_reason(records):
    rid = put(records, "fire-assessment", "warning", {"level": "orange"})
    r = check("publish_warning", warn(records, record_id=rid), records)
    assert not r["ok"] and "计算结果 橙色 不一致" in r["problems"][0]
    r = check("publish_warning", warn(records, record_id=rid, override_reason="现场发现明火"), records)
    assert r["ok"] and "理由：现场发现明火" in r["notes"][0]
    assert r["summary"]["override_reason"] == "现场发现明火" and r["summary"]["human_items"] == r["notes"]


def test_record_without_level(records):
    rid = put(records, "fire-assessment", "warning", {"nothing": 1})
    r = check("publish_warning", warn(records, record_id=rid), records)
    assert not r["ok"] and "没有可核对的预警等级" in r["problems"][0]


def test_both_ids_and_no_record_store(records):
    rid = put(records, "fire-assessment", "warning", {"level": "red"})
    r = check("publish_warning", warn(records, record_id=rid, snapshot_id="SN-x"), records)
    assert not r["ok"] and "只能给一个" in r["problems"][0]
    r = precheck("publish_warning", warn(records, record_id=rid), NOW, None, None)
    assert not r["ok"] and "没有配置记录目录" in r["problems"][0]


def test_escalation_records(records):
    yes = put(records, "emergency-escalation", "escalation", {"recommend": True})
    no = put(records, "emergency-escalation", "escalation", {"recommend": False})
    esc = lambda rid: {"escalation": {"reason": "红色预警", "recommended_level": "应急响应", "record_id": rid}}  # noqa: E731
    r = check("escalate_emergency", esc(yes), records)
    assert r["ok"] and r["summary"]["checks"][-1] == {"name": "升级建议", "ok": True, "detail": "记录的结论为建议升级"}
    r = check("escalate_emergency", esc(no), records)
    assert not r["ok"] and "不建议升级" in r["problems"][0]
    warning = put(records, "fire-assessment", "warning", {"level": "red"})
    r = check("escalate_emergency", esc(warning), records)
    assert not r["ok"] and "emergency-escalation/escalation" in r["problems"][0]


def test_malformed_arguments_still_have_summary(records):
    r = check("publish_warning", {"notice": {"level": "purple", "record_id": "RS-20261104T200000-aaaaaa"}}, records)
    assert not r["ok"] and r["summary"]["checks"][0]["ok"] is False
