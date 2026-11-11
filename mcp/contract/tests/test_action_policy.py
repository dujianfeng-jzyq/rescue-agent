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
    assert {p.tool for p in c} == {"publish_warning", "escalate_emergency", "notify_evacuation", "issue_dispatch_order", "issue_sealing_plan", "issue_firefighting_plan", "approve_unsealing"}
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


def test_only_field_actions_and_notices_need_confirmation():
    """2026-11-10 操作员决定：加密监测（观察跟进）不弹确认；影响矿井现场的处置和对外通知要人确认。"""
    assert BY_TOOL["start_monitoring"].level == ActionLevel.B
    assert "start_monitoring" not in ACTION_NAMES


def test_approval_titles_come_from_the_table():
    assert ACTION_NAMES["publish_warning"] == "发布预警等级"
    r = precheck("notify_evacuation", {}, None, None)
    assert r["summary"]["action"] == BY_TOOL["notify_evacuation"].name


def test_on_timeout_defaults_to_escalate_and_never_auto_executes_field_actions():
    """第五版：超时怎么办。缺省 escalate（只升级提醒、不执行）；自动处理要有批准超时和升级对象；非 C 级不能设；
    影响现场或不可逆的动作（预警广播、撤人、升级、封闭、调度、防灭火方案、启封）一律不自动执行。"""
    assert all(p.on_timeout in ("approve", "reject", "escalate") for p in POLICIES)
    for tool in ("publish_warning", "escalate_emergency", "notify_evacuation", "issue_dispatch_order", "issue_sealing_plan", "issue_firefighting_plan", "approve_unsealing"):
        assert BY_TOOL[tool].on_timeout == "escalate", tool
    assert all(p.on_timeout == "escalate" for p in POLICIES if p.level != ActionLevel.C)
    # 目前没有超时自动处理的动作：加密监测改为 B 级（不弹确认），其余 C 级超时只升级提醒
    assert [p.tool for p in POLICIES if p.on_timeout != "escalate"] == []
    assert all(p["on_timeout"] in ("approve", "reject", "escalate") for p in export()["policies"])
