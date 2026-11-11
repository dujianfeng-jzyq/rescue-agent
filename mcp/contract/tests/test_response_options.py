"""处置选项表（第五版）：与矿井的处置步骤、防灭火方案的步骤 key、导出文件一致。"""

import json
from pathlib import Path

import yaml
from rescue_contract.export_schema import BUNDLE_DATA, render_bundle_data
from rescue_contract.models import StepType
from rescue_contract.response_options import OPTIONS, choosable_for, export, options_for

ROOT = Path(__file__).resolve().parents[3]


def test_steps_are_known_step_types():
    for opts in OPTIONS.values():
        for o in opts:
            assert o.step in {s.value for s in StepType}, o.id


def test_ids_unique_within_level_and_levels_are_the_response_levels():
    assert set(OPTIONS) == {"yellow", "orange", "red"}
    for opts in OPTIONS.values():
        ids = [o.id for o in opts]
        assert len(set(ids)) == len(ids)


def test_orange_options_match_the_firefighting_plan_steps():
    """橙色的选项就是防灭火方案里的步骤：plan_key 必须是 plan.yaml 里的必备或可选步骤，type 一致。"""
    plan = yaml.safe_load((ROOT / "skills" / "firefighting-plan" / "references" / "plan.yaml").read_text(encoding="utf-8"))
    steps = {s["key"]: s for s in plan["required_steps"] + plan["optional_steps"]}
    required = {s["key"] for s in plan["required_steps"]}
    for o in options_for("orange"):
        assert o.plan_key in steps, o.id
        assert steps[o.plan_key]["type"] == o.step
        assert o.plan_key not in required  # 卡片上的处置都是方案的可选步骤：操作员不选就不进方案
        assert not o.always


def test_inert_is_suggested_not_mandatory():
    """2026-11-11：注氮由操作员选（标“建议”），不再必做；操作员可以明确选“不采取处置”。"""
    inert = next(o for o in options_for("orange") if o.id == "inert")
    assert inert.suggested and not inert.always and inert.choosable


def test_card_options_are_the_choosable_ones_only():
    assert [o.id for o in choosable_for("orange")] == ["inert", "grout", "leak_seal"]
    assert choosable_for("yellow") == [] and choosable_for("red") == []
    assert options_for("橙色") == options_for("orange") and options_for("none") == ()


def test_bundle_export_is_up_to_date():
    for filename, text in render_bundle_data().items():
        assert (BUNDLE_DATA / filename).exists(), f"缺少 {filename}，请运行 make schema"
        assert (BUNDLE_DATA / filename).read_text(encoding="utf-8") == text, f"{filename} 已过期，请运行 make schema"
    assert json.loads((BUNDLE_DATA / "response-options.json").read_text(encoding="utf-8")) == export()
