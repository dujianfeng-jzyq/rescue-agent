"""对外动作分级（docs/design-v3.md 6.3）：guard 的每个工具属于哪一级、谁确认、批准多久没人理就升级。

| 级别 | 动作 | 处理 |
| A 只读 | 取数、研判、检索、起草方案、预检 | 自动 |
| B 内部提示 | 给值班人员的告警、事件更新、报告交付 | 自动 |
| C 对外动作 | 预警广播、撤人通知、方案下发、调度下发、升级、启封审批 | 守卫校验 + 人工批准；弹窗显示依据 |

这张表是分级的唯一来源：DSH 审批插件按它决定哪些工具要弹确认（读 `dsh-bundle/data/action-policy.json`，由 export_schema 导出），
guard 的测试检查“每个工具都登记了、登记的都存在”。**没有登记的工具按 C 级处理**（fail closed）。

超时后怎么办（on_timeout，第五版）：approve 自动放行（只给低风险、可逆的动作，如加密监测）、reject 自动拒绝、escalate 只升级提醒不执行（缺省；影响现场或不可逆的动作一律不自动执行）。
确认人、批准超时、超时后升级给谁，都是占位值（TODO(专家)：演示后审定）。超时升级由 P3 的监听器执行
（写进事件时间线）；批准人按角色限定需要 DSH 的用户身份，P3/P4 再做，目前确认人只用于弹窗显示。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class ActionLevel(StrEnum):
    A = "A"  # 只读，自动
    B = "B"  # 内部提示，自动
    C = "C"  # 对外动作，守卫校验 + 人工批准


@dataclass(frozen=True)
class ActionPolicy:
    tool: str
    level: ActionLevel
    name: str  # 中文动作名（弹窗标题）
    confirmer: str | None = None  # 确认人角色（C 级）
    approval_timeout_min: int | None = None  # 批准超时（分钟，C 级）：超时未批准要提醒、升级
    escalate_to: str | None = None  # 超时后提醒/升级给谁
    on_timeout: str = "escalate"  # 批准超时后怎么办（第五版）：approve 自动放行、reject 自动拒绝、escalate 只升级提醒（缺省，不执行）


POLICIES: tuple[ActionPolicy, ...] = (
    # C 级：对外动作（弹确认；执行前 guard 再校验）
    ActionPolicy("publish_warning", ActionLevel.C, "发布预警等级", "通风部门负责人", 10, "值班长"),
    ActionPolicy("escalate_emergency", ActionLevel.C, "升级为应急响应", "总工程师或矿长", 15, "矿长"),
    ActionPolicy("notify_evacuation", ActionLevel.C, "发布补充撤人名单", "调度员", 5, "值班长"),
    ActionPolicy("issue_dispatch_order", ActionLevel.C, "下发物资调度单", "指挥部指定负责人", 15, "总指挥"),
    ActionPolicy("issue_sealing_plan", ActionLevel.C, "下发封闭方案", "总指挥 + 救护队带队指挥员", 30, "总指挥"),
    ActionPolicy("issue_firefighting_plan", ActionLevel.C, "下发防灭火方案", "总工程师", 30, "总指挥"),  # 确认人、批准超时、升级对象均为占位值
    ActionPolicy("approve_unsealing", ActionLevel.C, "批准启封", "总指挥", 30, "矿长"),
    # A 级：只读（不弹确认；审计仍记录）
    ActionPolicy("precheck_plan", ActionLevel.A, "方案红线自查"),
    ActionPolicy("query_last_issued", ActionLevel.A, "查询最近一次下发"),
    # B 级（给值班人员的告警、事件更新）随第三版 P3 的 incident 插件加入
)

ON_TIMEOUT = ("approve", "reject", "escalate")
for _p in POLICIES:
    assert _p.on_timeout in ON_TIMEOUT, f"{_p.tool} 的 on_timeout 不合法：{_p.on_timeout}"
    # 只有 C 级（要人批准的）有“超时怎么办”；自动放行必须有批准超时和升级对象（放行后仍要让上级知道）
    assert _p.level == ActionLevel.C or _p.on_timeout == "escalate", f"{_p.tool}：非 C 级不能设 on_timeout"
    assert _p.on_timeout == "escalate" or (_p.approval_timeout_min and _p.escalate_to), f"{_p.tool}：自动处理要有批准超时和升级对象"

BY_TOOL: dict[str, ActionPolicy] = {p.tool: p for p in POLICIES}
assert len(BY_TOOL) == len(POLICIES), "action_policy 里有重复的工具"

# 弹窗标题用的中文动作名（C 级）
ACTION_NAMES: dict[str, str] = {p.tool: p.name for p in POLICIES if p.level == ActionLevel.C}


def level_of(tool: str) -> ActionLevel:
    """工具的级别；没有登记的按 C 级处理（fail closed）。"""
    p = BY_TOOL.get(tool)
    return p.level if p else ActionLevel.C


def export() -> dict:
    """导出给 TS 侧（审批插件）的 JSON 内容。"""
    return {
        "$comment": "由 rescue_contract.action_policy 导出；没有登记的工具按 C 级处理",
        "levels": {"A": "只读，自动", "B": "内部提示，自动", "C": "对外动作：守卫校验 + 人工批准"},
        "policies": [{**asdict(p), "level": p.level.value} for p in POLICIES],
    }
