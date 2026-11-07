"""eval/agent/facts.py：固定流程的关键事实检查。要点：措辞润色放行，事实被改、被漏、被相反说法顶替就抓住。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval" / "agent"))
import facts  # noqa: E402

ASSESS = {
    "record_ids": {"warning": "RS-20261104T080000-qreyji", "escalation": "RS-20261104T080000-1chs65"},
    "result": {
        "warning": {"level_name": "无", "stage_name": "缓慢氧化"},
        "alarms": [],
        "explosion": {
            "overall_name": "可能进入爆炸范围（减风积聚或混入空气）",
            "worst_sensor_id": "BT-1201-G1",
            "points": [{"sensor_id": "BT-1201-G1", "zone": "II", "zone_name": "减风危险区"}],
        },
        "personnel": {"computed": False, "note": "预警等级未达到橙色（严重），未计算受威胁人员"},
        "escalation": {"recommend": False, "articles": [{"document": "矿山救援规程", "article": "第39条"}, {"document": "矿山救援规程", "article": "第40条"}]},
    },
}

BRIEF = """【研判要点】1201 工作面 2026-11-04 08:00:00

- 预警等级：无；自燃发展阶段：缓慢氧化
- 实时报警：无
- 爆炸危险性：可能进入爆炸范围（减风积聚或混入空气）
- 最不利测点：BT-1201-G1，II 区 减风危险区
- 受威胁人员：预警等级未达到橙色（严重），未计算受威胁人员
- 升级建议：暂不建议升级为应急响应
- 升级建议的规程依据：矿山救援规程第39条、矿山救援规程第40条
- 研判说明：预警等级无、自燃阶段缓慢氧化，暂不建议升级。

依据：记录 RS-20261104T080000-qreyji（预警等级）、RS-20261104T080000-1chs65（升级建议）"""


def check(tool, payload, reply):
    return facts.check_reply(facts.facts_for(tool, payload), reply)


def test_exact_brief_passes():
    r = check("run_fire_assessment", ASSESS, BRIEF)
    assert not r["missing"] and not r["contradicted"], r
    assert r["total"] >= 10


def test_rewording_and_formatting_pass():
    reply = BRIEF.replace("预警等级无、自燃阶段缓慢氧化", "预警等级为无、自燃阶段为缓慢氧化").replace("- ", "* ").replace("预警等级：无", "**预警等级**：无")
    r = check("run_fire_assessment", ASSESS, reply)
    assert not r["missing"] and not r["contradicted"], r


@pytest.mark.parametrize(
    "edits,missing",
    [
        ([("缓慢氧化", "加速氧化")], "自燃发展阶段"),
        ([("预警等级：无", "预警等级：橙色"), ("预警等级无", "预警等级橙色")], "预警等级"),
        ([("BT-1201-G1，II 区", "BT-1201-G2，II 区")], "最不利测点"),
        ([("矿山救援规程第40条", "矿山救援规程第41条")], "条文依据 第40条"),
        ([("RS-20261104T080000-1chs65", "RS-20261104T080000-xxxxxx")], "记录编号（升级建议）"),
    ],
)
def test_changed_fact_is_caught(edits, missing):
    reply = BRIEF
    for old, new in edits:
        assert old in reply
        reply = reply.replace(old, new)
    r = check("run_fire_assessment", ASSESS, reply)
    assert any(missing in m for m in r["missing"]), r


def test_dropped_line_is_caught():
    reply = "\n".join(line for line in BRIEF.splitlines() if "爆炸危险性" not in line)
    assert "爆炸危险性" in check("run_fire_assessment", ASSESS, reply)["missing"]


def test_recommend_vs_not_recommend():
    # “建议升级”被“不建议升级”包含：真实结论是不建议，回复写成建议升级要判失败
    r = check("run_fire_assessment", ASSESS, BRIEF.replace("暂不建议升级", "建议升级"))
    assert "升级建议：暂不建议升级" in r["missing"]
    rec = {**ASSESS, "result": {**ASSESS["result"], "escalation": {"recommend": True, "articles": []}}}
    ok = check("run_fire_assessment", rec, "建议升级为应急响应。预警等级无，缓慢氧化")
    assert "升级建议：建议升级" not in ok["missing"] + ok["contradicted"]
    bad = check("run_fire_assessment", rec, "暂不建议升级为应急响应")
    assert "升级建议：建议升级" in bad["missing"] + bad["contradicted"]


EFFECT = {
    "record_ids": {"effect": "RS-20261105T080000-banc5k"},
    "result": {
        "overall_name": "需继续处置",
        "since": "2026-11-04T08:00:00",
        "observed_h": 24,
        "counts": {"已受控": 2, "需继续处置": 6, "恶化": 0, "观测时间不足，暂不下结论": 0},
        "next_review_at": "2026-11-05T20:00:00",
        "escalation_suggested": False,
    },
}
EFFECT_BRIEF = """【效果评估要点】1201 工作面 2026-11-05 08:00:00

- 结论：需继续处置（计算结果）
- 处置开始：2026-11-04T08:00:00（指挥人员给出）；已观测 24 小时
- 各结论测点数：已受控 2 个、需继续处置 6 个、恶化 0 个、观测时间不足 0 个
- 下次复评：2026-11-05T20:00:00

依据：记录 RS-20261105T080000-banc5k（效果复评）"""


def test_effect_passes_and_date_forms():
    assert not check("review_effect", EFFECT, EFFECT_BRIEF)["missing"]
    # 2026-11-04 08:00 与 2026-11-04T08:00:00 是同一时刻
    assert not check("review_effect", EFFECT, EFFECT_BRIEF.replace("2026-11-04T08:00:00", "2026-11-04 08:00").replace("2026-11-05T20:00:00", "2026-11-05 20:00"))["missing"]


@pytest.mark.parametrize(
    "old,new",
    [("已受控 2 个", "已受控 3 个"), ("恶化 0 个", "恶化 1 个"), ("已观测 24 小时", "已观测 28 小时"), ("结论：需继续处置", "结论：已受控")],
)
def test_effect_changed_numbers_caught(old, new):
    assert check("review_effect", EFFECT, EFFECT_BRIEF.replace(old, new))["missing"]


UNSEAL_NO = {
    "record_ids": {"unseal": "RS-20261112T080000-74fsha"},
    "result": {"ready": False, "total_count": 6, "met_count": 5, "unmet_count": 1, "unmet_labels": "封闭时长"},
}
UNSEAL_YES = {"record_ids": {"unseal": "RS-1"}, "result": {"ready": True, "total_count": 6, "met_count": 6, "unmet_count": 0, "unmet_labels": ""}}


def test_unsealing_ready_not_ready():
    ok = "结论：不具备启封条件；共 6 项判定：5 项满足，1 项不满足。不满足的项：封闭时长。记录 RS-20261112T080000-74fsha"
    assert not check("assess_unsealing", UNSEAL_NO, ok)["missing"]
    # 真实结论是不具备，回复写成具备
    assert check("assess_unsealing", UNSEAL_NO, ok.replace("不具备启封条件", "具备启封条件"))["missing"]
    # 真实结论是具备，回复里出现“不具备”
    r = check("assess_unsealing", UNSEAL_YES, "结论：不具备启封条件；共 6 项判定：6 项满足，0 项不满足。RS-1")
    assert r["missing"] or r["contradicted"]
    assert not check("assess_unsealing", UNSEAL_YES, "结论：具备启封条件；共 6 项判定：6 项满足，0 项不满足。RS-1")["missing"]


def test_no_result_means_no_facts():
    assert facts.facts_for("run_fire_assessment", {"error": "取数失败"}) == []
    assert facts.facts_for("check_mine", {"result": {}}) == []


def test_numbers_do_not_glue_across_lines():
    # 24 后面换行接 2026：不能把“24”当成“242026”的一部分，也不能把“已观测 2”当成“已观测 24”
    assert check("review_effect", EFFECT, EFFECT_BRIEF.replace("已观测 24 小时", "已观测 2 小时"))["missing"]
