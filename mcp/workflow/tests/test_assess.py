"""W1 研判快照：步骤固定、结果可重复、报告只写结果里的数。"""

import json

import grade
import pytest
from rescue_workflow.server import build_server
from wf_helpers import build_mine, call, sim


def assess(scenario: str, hours: float, store, **args):
    r = call(build_server(build_mine(sim(scenario, hours)), store), "assess_panel", args)
    assert not r.is_error, r.content[0].text
    return r.structured_content


def _strip_charts(result: dict) -> dict:
    x = json.loads(json.dumps(result))
    x["explosion"].pop("chart_markdown")
    for p in x["explosion"]["points"]:
        p.pop("chart", None)
    return x


def test_same_time_same_result(store):
    a, b = assess("s4-escalation", 55, store), assess("s4-escalation", 55, store)
    assert _strip_charts(a["result"]) == _strip_charts(b["result"])
    assert a["snapshot_id"] != b["snapshot_id"]


def test_snapshot_is_stored(store):
    out = assess("s2-slow-oxidation", 48, store)
    snap = store.get(out["snapshot_id"])
    assert snap.kind == "assessment" and snap.panel == "1201"
    assert snap.report_markdown == out["report_markdown"]
    assert snap.result["warning"]["level"] == out["result"]["warning"]["level"]


@pytest.mark.parametrize(
    "scenario,hours,level,stage,personnel",
    [
        ("s2-slow-oxidation", 48, "none", "缓慢氧化", False),
        ("s4-escalation", 55, "red", "加速氧化", True),
    ],
)
def test_key_fields(store, scenario, hours, level, stage, personnel):
    r = assess(scenario, hours, store)["result"]
    assert r["warning"]["level"] == level
    assert r["warning"]["stage_name"] == stage
    assert (r["personnel"] is not None) == personnel
    assert r["trends"], "至少有默认趋势"
    assert r["explosion"]["overall"] in {"explosive", "may_become_explosive", "not_explosive", "unknown"}


def test_trends_follow_triggered_indicators(store):
    """红色由束管 CO 和乙烯触发：趋势取它们的来源测点，不多不少。"""
    r = assess("s4-escalation", 55, store)["result"]
    src = r["warning"]["indicator_sources"]
    expect = {(src["束管CO最大值(ppm)"]["sensor_id"], "CO"), (src["乙烯(ppm)"]["sensor_id"], "C2H4")}
    assert {(t["sensor_id"], t["measurand"]) for t in r["trends"]} == expect


def test_include_personnel_on_request(store):
    assert assess("s2-slow-oxidation", 48, store, include_personnel=True)["result"]["personnel"] is not None


@pytest.mark.parametrize("scenario,hours", [("s2-slow-oxidation", 48), ("s4-escalation", 55), ("s6-sensor-faults", 30)])
def test_report_numbers_come_from_result(store, scenario, hours):
    """报告里的每个数都能在结构化结果里找到（与智能体评测的数值溯源同一套规则）。"""
    out = assess(scenario, hours, store)
    pool = {tok for tok, _ in grade.numbers(json.dumps(out["result"], ensure_ascii=False))}
    pool |= {tok for tok, _ in grade.numbers(out["snapshot_id"])}
    text = out["report_markdown"].split("\n\n![")[0]  # 风险图链接里的文件名不算
    missing = [tok for tok, _ in grade.numbers(text) if grade.match_level(tok, pool) != "exact"]
    assert missing == []


def test_report_cites_no_uncalled_service(store):
    """报告不点名 workflow 在内部调用的服务（智能体没有直接调用它们，评测会判为点名未调用）。"""
    text = assess("s4-escalation", 55, store)["report_markdown"]
    for name in ("fire_model", "ventnet", "mine_data", "assess_warning_level", "gas_trend", "threatened_personnel"):
        assert name not in text


def test_explosion_unknown_when_a_tube_fails(store, monkeypatch):
    from rescue_fire_model import tube
    from rescue_workflow import assess as wf

    real = tube.assess_tube

    async def flaky(md, sensor_id, *a, **k):
        if sensor_id.endswith("G3"):
            from mcp.server.mcpserver.exceptions import ToolError

            raise ToolError("测试：没有完整读数")
        return await real(md, sensor_id, *a, **k)

    monkeypatch.setattr(wf.tube, "assess_tube", flaky)
    x = assess("s4-escalation", 55, store)["result"]["explosion"]
    assert x["overall"] == "unknown"
    assert [f["sensor_id"] for f in x["failures"]] == ["BT-1201-G3"]


def test_report_template_matches_skill(store):
    """skills/shared/report-templates 的“预警研判报告”与代码模板的各行标签一致。"""
    from pathlib import Path

    skill = (Path(__file__).resolve().parents[3] / "skills" / "shared" / "report-templates" / "SKILL.md").read_text(encoding="utf-8")
    section = skill.split("## 预警研判报告", 1)[1].split("\n## ", 1)[0]
    template = section.split("```", 2)[1]
    labels = [line.split("<", 1)[0].split("：", 1)[0] for line in template.strip().splitlines() if "：" in line and not line.startswith("- ")]
    text = assess("s4-escalation", 55, store)["report_markdown"]
    for label in labels:
        assert label in text, label
    assert text.splitlines()[0].startswith("【预警研判】")
