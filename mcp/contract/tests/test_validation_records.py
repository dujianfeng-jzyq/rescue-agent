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
    return precheck(tool, args, now, records)


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


def test_record_id_is_required_and_record_dir_must_exist(records):
    r = check("publish_warning", {"notice": {"location": "1201", "level": "red", "summary": "s"}}, records)
    assert not r["ok"] and "数据契约" in r["problems"][0]  # 第一版的 snapshot_id 已删除：没有 record_id 就是格式错误
    rid = put(records, "fire-assessment", "warning", {"level": "red"})
    r = precheck("publish_warning", warn(records, record_id=rid), NOW, None)
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


def test_summary_facts_come_from_records_and_arguments(records):
    """弹窗要点由代码从记录（或撤人名单的参数）取：确认人看得到要批的是什么。"""
    rid = put(records, "fire-assessment", "warning", {"level": "red", "level_name": "红色（危险）", "triggered": ["红色（危险）：乙烯(ppm) 2.09 ≥ 2.0"]})
    r = check("publish_warning", warn(records, record_id=rid), records)
    assert r["summary"]["facts"] == ["预警等级：红色（危险）", "触发条件：红色（危险）：乙烯(ppm) 2.09 ≥ 2.0"]
    esc = put(records, "emergency-escalation", "escalation", {"recommend": True, "reasons": ["预警等级 红色（危险），达到升级条件（不低于 红色（危险））"], "articles": [{"document": "矿山救援规程", "article": "第39条"}]})
    r = check("escalate_emergency", {"escalation": {"reason": "x", "recommended_level": "应急响应", "record_id": esc}}, records)
    assert r["summary"]["facts"] == ["升级理由：预警等级 红色（危险），达到升级条件（不低于 红色（危险））", "规程依据：第39条"]
    evac = {"notice": {"reason": "x", "persons": ["P1", "P2", "P3"], "routes": {"P1": ["N1", "N8"], "P2": ["N2", "N8"]}}}
    assert check("notify_evacuation", evac, records)["summary"]["facts"] == ["补充撤人名单 3 人", "撤往：N8", "没有可达避灾路线 1 人：P3（需人工研判）"]
    # 引用了人员记录：去向写中文名（从记录的避灾路线取）
    pe = put(records, "fire-assessment", "personnel", {"threatened": [{"persons": ["P1"], "escape_route": {"reachable": True, "nodes": ["N1", "N8"], "target": "N8", "target_name": "避难硐室"}}]})
    evac = {"notice": {"reason": "x", "record_id": pe, "persons": ["P1"], "routes": {"P1": ["N1", "N8"]}}}
    assert check("notify_evacuation", evac, records)["summary"]["facts"] == ["补充撤人名单 1 人", "撤往：避难硐室（N8）"]
    r = check("publish_warning", warn(records, record_id="RS-20261104T200000-zzzzzz"), records)
    assert not r["ok"] and r["summary"]["facts"] == []  # 没通过预检的不给要点


def test_evacuation_reason_is_generated_from_the_record():
    """撤人通知正文按记录生成：人数、去向、走不通的人、“补充、不替代预案撤人”。"""
    from rescue_contract.validation import evacuation_reason

    result = {
        "panel": "1201",
        "threatened": [
            {"persons": ["P1", "P2"], "escape_route": {"reachable": True, "nodes": ["N3", "N2", "N8"], "target": "N8", "target_name": "1201避难硐室"}},
            {"persons": ["P3"], "escape_route": {"reachable": False}},
        ],
    }
    text = evacuation_reason(result, ["P1", "P2", "P3"], {"P1": ["N3", "N8"], "P2": ["N3", "N8"]}, "RS-20261104T200000-aaaaaa")
    assert text == (
        "1201 工作面补充撤人名单 3 人，撤往 1201避难硐室；其中 1 人（P3）避开受污染巷道后没有可达避灾路线，需人工研判；"
        "本通知是预案撤人的补充，不替代、不延迟预案撤人；依据人员记录 RS-20261104T200000-aaaaaa。"
    )
