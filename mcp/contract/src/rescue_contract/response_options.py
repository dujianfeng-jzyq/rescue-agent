"""处置选项表（第五版，docs/design-v5-demo-flow.md）：每一级预警操作员可以选哪些处置，以及它们对应矿井的哪种处置步骤。

单一来源：智能体的 `get_response_options` 工具、演示导演（按矿井实际执行的处置识别操作员选了什么）、防灭火方案流程的可选步骤校验
都读这张表。导出为 `dsh-bundle/data/response-options.json`（改表后 `make schema`）。全部是占位值，待专家审定。

字段：
- id、label：选项编号和给人看的名字（提问卡片用 label）；
- step：对应的矿井处置步骤类型（StepType 取值）——导演据此认出“这一项已经下发”；
- how：智能体用哪个流程/工具落实（告诉智能体，不是权限）；
- plan_key：防灭火方案里对应的步骤 key（skills/firefighting-plan/references/plan.yaml）；
- always：True 表示这一项一定包含（不放进选择卡片，只在卡片说明里告知）。目前没有这样的项：橙色的注氮原来是必做，
  2026-11-11 改为由操作员选（卡片上标“建议”），操作员也可以明确选“不采取处置”（安全取舍由项目负责人决定，待专家审定）；
- suggested：True 表示卡片上标“（建议）”（仍由操作员选，不预先勾选）；
- choosable：False 表示不由卡片选择（智能体按流程自己落实，导演只识别结果）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ResponseOption:
    id: str
    label: str
    step: str
    how: str
    plan_key: str | None = None
    always: bool = False
    choosable: bool = True
    suggested: bool = False


# 预警等级 → 选项（等级名用代码里的 yellow / orange / red）
OPTIONS: dict[str, tuple[ResponseOption, ...]] = {
    "yellow": (
        ResponseOption("monitor", "观察跟进（加密监测）", "monitor", "start_monitoring", choosable=False),
    ),
    "orange": (
        ResponseOption("inert", "注氮", "inert_injection", "draft_firefighting_plan → issue_firefighting_plan", plan_key="inert_injection", suggested=True),
        ResponseOption("grout", "注浆", "grouting", "draft_firefighting_plan → issue_firefighting_plan", plan_key="grouting"),
        ResponseOption("leak_seal", "堵漏", "leak_sealing", "draft_firefighting_plan → issue_firefighting_plan", plan_key="leak_sealing"),
    ),
    "red": (
        ResponseOption("evacuate", "撤人", "evacuate", "notify_evacuation", choosable=False),
        ResponseOption("seal", "封闭火区", "temporary_seal", "draft_sealing_plan → issue_sealing_plan", choosable=False),
    ),
}

LEVEL_NAMES = {"yellow": "黄色", "orange": "橙色", "red": "红色"}
LEVEL_BY_NAME = {v: k for k, v in LEVEL_NAMES.items()}


def options_for(level: str) -> tuple[ResponseOption, ...]:
    """level 取 yellow / orange / red（或中文黄色、橙色、红色）；其他等级没有处置选项。"""
    return OPTIONS.get(LEVEL_BY_NAME.get(level, level), ())


def choosable_for(level: str) -> list[ResponseOption]:
    return [o for o in options_for(level) if o.choosable and not o.always]


def export() -> dict:
    return {
        "$comment": "由 rescue_contract.response_options 导出；占位值，待专家审定",
        "levels": {lv: [asdict(o) for o in opts] for lv, opts in OPTIONS.items()},
    }
