"""rescue-mine 接口表：接口版本、工具清单、分组、读写属性、可选功能。

这是 rescue-mine 接口的唯一来源（docs/design-v3.md 第 4 节）：
- 一致性检查（conformance）按它检查工具是否齐全、自描述（get_capabilities）是否如实；
- 令牌范围（serving）按它区分只读工具和写入工具：写入工具要另一个令牌，只有 guard 持有；
- 接口说明文档（docs/rescue-mine-interface.md）按它和实际服务的工具定义生成（interface_doc）。

rescue-mine 只有“读数据”和“执行动作”，不含策略：不做红线校验，不做研判，不存指令台账。
对接新矿井就是实现这些工具；可选功能（feature）不提供时，对应工具可以不实现，由 get_capabilities 如实说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 接口版本：工具集合或语义不兼容地变化时升主版本；与 CONTRACT_VERSION（数据模型版本）分开
INTERFACE_VERSION = "1.0.0"

Access = Literal["read", "write"]

# 可选功能（get_capabilities 的 features）
FEATURE_ALARMS = "alarms"
FEATURE_EQUIPMENT = "equipment"
FEATURE_NOTICE = "notice"
FEATURE_DISPATCH = "dispatch"
FEATURE_TREATMENT = "treatment"
FEATURES = (FEATURE_ALARMS, FEATURE_EQUIPMENT, FEATURE_NOTICE, FEATURE_DISPATCH, FEATURE_TREATMENT)
# 提供其中任何一项，就必须提供 get_action_status（回执要能查）
ACTION_FEATURES = frozenset({FEATURE_NOTICE, FEATURE_DISPATCH, FEATURE_TREATMENT})
STATUS_TOOL = "get_action_status"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    group: str
    access: Access
    summary: str
    feature: str | None = None  # 属于哪个可选功能；None 表示必需


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("get_capabilities", "自描述", "read", "接口版本、本矿实际提供的工具和可选功能"),
    ToolSpec("describe_mine", "自描述", "read", "工作面拓扑（进回风巷、采空区、避难硐室）和测点用到的语义角色"),
    ToolSpec("get_clock", "时间", "read", "矿井时钟（模拟加速时即模拟时间）；研判一律以此为“现在”"),
    ToolSpec("list_sensors", "监测数据", "read", "全部测点：来源、可测参数、所在巷道、所属工作面、语义角色"),
    ToolSpec("get_latest", "监测数据", "read", "各测点最新读数（含数据质量标记）"),
    ToolSpec("get_readings", "监测数据", "read", "测点历史读数（时间窗、可按参数筛选）"),
    ToolSpec("get_events", "监测数据", "read", "矿井监控系统自身产生的事件：超报警值、测点故障"),
    ToolSpec("get_fire_areas", "监测数据", "read", "火区管理台账：封闭过的火区、状态、封闭和启封时间"),
    ToolSpec("get_network", "通风网络", "read", "通风网络：节点（含井口、避难硐室）和巷道"),
    ToolSpec("get_personnel", "人员物资", "read", "井下人员实时位置，含总数、班组、巷道汇总"),
    ToolSpec("get_inventory", "人员物资", "read", "应急物资台账"),
    ToolSpec("get_rescue_teams", "人员物资", "read", "救援力量台账"),
    ToolSpec("get_alarms", "矿井自带报警", "read", "矿井报警平台的报警（拉取，含确认状态）", FEATURE_ALARMS),
    ToolSpec("get_equipment_status", "设备", "read", "风机、水泵、制氮、注浆设备的运行状态", FEATURE_EQUIPMENT),
    ToolSpec("send_notice", "通知与指令", "write", "发送预警、撤人等通知，返回回执", FEATURE_NOTICE),
    ToolSpec("dispatch_order", "通知与指令", "write", "下发物资、人员调度，返回回执", FEATURE_DISPATCH),
    ToolSpec("submit_treatment", "通知与指令", "write", "提交处置动作（注惰、灌浆、封闭等），返回回执", FEATURE_TREATMENT),
    ToolSpec("get_action_status", "通知与指令", "read", "按回执编号查询通知、指令的执行状态（异步生效）；提供任一通知/指令功能时必需", "action_status"),
)

BY_NAME = {t.name: t for t in TOOLS}
ALL_TOOLS = frozenset(BY_NAME)
REQUIRED_TOOLS = frozenset(t.name for t in TOOLS if t.feature is None)  # 无论提供哪些可选功能都必需
WRITE_TOOLS = frozenset(t.name for t in TOOLS if t.access == "write")


def tools_for(features: set[str] | list[str] | tuple[str, ...]) -> set[str]:
    """提供这些可选功能时，应当实现的工具集合（必需工具 + 各功能的工具）。"""
    wanted = set(features)
    out = {t.name for t in TOOLS if t.feature is None or t.feature in wanted}
    if wanted & ACTION_FEATURES:
        out.add(STATUS_TOOL)
    return out
