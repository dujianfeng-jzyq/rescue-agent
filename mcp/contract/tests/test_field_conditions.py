"""封闭方案人工确认项的预设卡片（第五版）：字段与技能一致，选项合法，导出文件最新。"""

import importlib.util
import json
import sys
from pathlib import Path

from rescue_contract.export_schema import BUNDLE_DATA, render_bundle_data
from rescue_contract.field_conditions import CONFIRMERS, FIELDS, export

ROOT = Path(__file__).resolve().parents[3]


def _skeleton_fields() -> dict:
    scripts = str(ROOT / "skills" / "sealing-plan" / "scripts")
    sys.path.insert(0, scripts)  # 脚本 import 同目录的辅助模块（运行时是 python -E -s -B 直接跑脚本，目录在 sys.path 里）
    try:
        spec = importlib.util.spec_from_file_location("sealing_skeleton", Path(scripts) / "skeleton.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod.HUMAN_FIELDS
    finally:
        sys.path.remove(scripts)
        for name in ("_evidence", "_quality", "_skill"):  # 别让别的技能的同名辅助模块读到这份
            sys.modules.pop(name, None)


def test_fields_match_the_sealing_skill():
    human = _skeleton_fields()
    assert [f.field for f in FIELDS] == list(human)
    for f in FIELDS:
        assert f.value_type == human[f.field][1]


def test_choice_values_have_the_right_type_and_labels_are_unique():
    for f in FIELDS:
        labels = [c.label for c in f.choices]
        assert len(set(labels)) == len(labels) and labels
        for c in f.choices:
            assert isinstance(c.value, bool) if f.value_type == "bool" else (isinstance(c.value, float) and not isinstance(c.value, bool)), (f.field, c)


def test_confirmers_cover_the_sealing_plan_confirmer():
    from rescue_contract.action_policy import BY_TOOL

    for who in BY_TOOL["issue_sealing_plan"].confirmer.replace(" ", "").split("+"):
        assert who in CONFIRMERS or who == "救护队带队指挥员" and "救护队带队指挥员" in CONFIRMERS


def test_bundle_export_is_up_to_date():
    for filename, text in render_bundle_data().items():
        assert (BUNDLE_DATA / filename).exists(), f"缺少 {filename}，请运行 make schema"
        assert (BUNDLE_DATA / filename).read_text(encoding="utf-8") == text, f"{filename} 已过期，请运行 make schema"
    assert json.loads((BUNDLE_DATA / "field-conditions.json").read_text(encoding="utf-8")) == export()
