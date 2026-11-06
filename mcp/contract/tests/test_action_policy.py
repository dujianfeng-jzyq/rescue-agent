"""动作分级表（docs/design-v3.md 6.3）：表、导出文件、guard 的工具三者一致。"""

import json

from rescue_contract.action_policy import ACTION_NAMES, BY_TOOL, POLICIES, ActionLevel, export, level_of
from rescue_contract.export_schema import BUNDLE_DATA, render_bundle_data
from rescue_contract.validation import precheck


def test_unregistered_tools_are_level_c():
    """没有登记的工具按 C 级处理（fail closed）：新增对外动作忘了登记，只会多弹一次确认，不会漏掉。"""
    assert level_of("brand_new_action") == ActionLevel.C
    assert level_of("precheck_plan") == ActionLevel.A


def test_every_c_level_action_has_confirmer_timeout_and_escalation():
    c = [p for p in POLICIES if p.level == ActionLevel.C]
    assert {p.tool for p in c} == {"publish_warning", "escalate_emergency", "notify_evacuation", "issue_dispatch_order", "issue_sealing_plan", "approve_unsealing"}
    for p in c:
        assert p.confirmer and p.approval_timeout_min and p.approval_timeout_min > 0 and p.escalate_to, p.tool
    assert all(p.confirmer is None for p in POLICIES if p.level != ActionLevel.C)


def test_bundle_export_is_up_to_date():
    """审批插件运行时读 dsh-bundle/data/action-policy.json：必须和表一致（改了表要运行 make schema）。"""
    for filename, text in render_bundle_data().items():
        path = BUNDLE_DATA / filename
        assert path.exists(), f"缺少 {filename}，请运行 make schema"
        assert path.read_text(encoding="utf-8") == text, f"{filename} 已过期，请运行 make schema"
    assert json.loads((BUNDLE_DATA / "action-policy.json").read_text(encoding="utf-8")) == export()


def test_approval_titles_come_from_the_table():
    assert ACTION_NAMES["publish_warning"] == "发布预警等级"
    r = precheck("notify_evacuation", {}, None, None)
    assert r["summary"]["action"] == BY_TOOL["notify_evacuation"].name
