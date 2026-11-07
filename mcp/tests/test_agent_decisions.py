"""eval/agent/decisions.py：期望决策 B 与实际决策 B1 的比较。重点是各种偏差都能抓住，正常情形不误报。"""

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval" / "agent"))
import decisions  # noqa: E402

A = "mcp__actions__"
EXPECT = {
    "external_actions": {
        "required": {"escalate_emergency": {}, "publish_warning": {"level": "red"}},
        "optional": {"notify_evacuation": {}},
    },
    "flows_called": ["run_fire_assessment"],
    "incident_ops": {"required": [], "forbidden": ["close_incident"]},
}


def call(tool, **args):
    return (tool, json.dumps(args, ensure_ascii=False))


GOOD = [
    call("get_incident", incident_id="INC-1"),
    call("run_fire_assessment", panel="1201"),
    call(A + "publish_warning", notice={"level": "red", "record_id": "RS-1"}),
    call(A + "escalate_emergency", escalation={"recommended_level": "红色（危险）", "record_id": "RS-2"}),
]


def run(calls, expect=EXPECT):
    return decisions.compare(expect, calls)


def test_good_run_passes():
    r = run(GOOD)
    assert r["ok"], r
    assert r["actual"]["publish_warning"] == [{"level": "red"}]


def test_optional_action_is_tolerated():
    r = run(GOOD + [call(A + "notify_evacuation", notice={"names": []})])
    assert r["ok"], r


def test_missing_required_action():
    r = run([c for c in GOOD if "escalate_emergency" not in c[0]])
    assert not r["ok"] and "漏做：没有提交 escalate_emergency" in r["problems"]


def test_nothing_submitted_fails():
    r = run([GOOD[0], GOOD[1]])
    assert len([p for p in r["problems"] if p.startswith("漏做")]) == 2


def test_unexpected_external_action_is_caught():
    r = run(GOOD + [call(A + "issue_sealing_plan", record_id="RS-9")])
    assert not r["ok"] and any("多做" in p and "issue_sealing_plan" in p for p in r["problems"])


def test_wrong_param_is_caught_on_every_submission():
    r = run(GOOD + [call(A + "publish_warning", notice={"level": "orange", "record_id": "RS-1"})])
    assert not r["ok"] and any("publish_warning 第 2 次提交" in p and "orange" in p for p in r["problems"])


def test_flow_not_called():
    r = run([c for c in GOOD if c[0] != "run_fire_assessment"])
    assert "没有调用流程 run_fire_assessment" in r["problems"]


def test_forbidden_and_required_incident_ops():
    r = run(GOOD + [call("close_incident", incident_id="INC-1")])
    assert "不该做：调用了 close_incident" in r["problems"]
    expect = {**EXPECT, "incident_ops": {"required": ["complete_followup"], "forbidden": []}}
    assert "漏做：没有调用事件操作 complete_followup" in run(GOOD, expect)["problems"]
    assert run(GOOD + [call("complete_followup", followup_id="F")], expect)["ok"]


def test_read_only_actions_are_not_external():
    r = run(GOOD + [call(A + "precheck_plan", plan={}), call(A + "query_last_issued", panel="1201")])
    assert r["ok"], r


def test_closed_set_with_empty_required_flags_any_submission():
    expect = {"external_actions": {"required": {}, "optional": {}}}
    assert run([call("run_fire_assessment")], expect)["ok"]
    assert not run([call(A + "publish_warning", notice={"level": "red"})], expect)["ok"]


def test_unspecified_sections_are_not_checked():
    assert run([call(A + "publish_warning", notice={"level": "red"})], {"flows_called": []})["ok"]


def test_unparseable_arguments_do_not_crash():
    r = run(GOOD + [(A + "publish_warning", "not json")])
    assert not r["ok"]  # 没有 level：参数不符


def test_cases_yaml_blocks_are_well_formed():
    cases = yaml.safe_load((Path(__file__).resolve().parents[2] / "eval" / "agent" / "cases.yaml").read_text(encoding="utf-8"))
    blocks = [(c["id"], c["checks"]["expect_decisions"]) for c in cases if "expect_decisions" in (c.get("checks") or {})]
    assert {i for i, _ in blocks} >= {"v3-wake-anomaly", "s4-watch-wake-assess", "v3-followup-due"}
    for cid, b in blocks:
        assert b["status"] == "placeholder", cid  # 在专家审定前必须标占位
        assert set(b) <= {"status", "derived_from", "external_actions", "flows_called", "incident_ops"}, cid
        ext = b.get("external_actions") or {}
        assert not (set(ext.get("required") or {}) & set(ext.get("optional") or {})), cid
