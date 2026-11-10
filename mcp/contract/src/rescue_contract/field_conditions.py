"""封闭方案的人工确认项：给操作员的预设选项卡片（第五版）。

封闭方案有 5 项现场条件只能由人在现场确认（系统没有数据，规则不允许智能体替人填）：灾区温度、能见度、火源是否明确、
水源/人力/物力是否充足、回风道是否畅通，再加上确认人。原来由智能体临时编提问，操作员要手敲 5 个值；这里由代码给出
一张卡片，常用值做成选项（也可以在“其他”里直接输入实际数值）。选的就是操作员对现场的确认，不是替人填：
卡片上写明“按现场实际选择或输入”，确认人必选，值和确认人一起进方案记录。

字段与 skills/sealing-plan/scripts/skeleton.py 的 HUMAN_FIELDS 一致（有测试）。导出为 `dsh-bundle/data/field-conditions.json`（改后 `make schema`）。
选项里的数值只是常用档位，不是阈值，也不是结论；红线（第88条等）在 redlines.py 里按实际输入判定。占位值，待专家审定。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Choice:
    label: str
    value: float | bool


@dataclass(frozen=True)
class FieldCard:
    field: str
    value_type: str  # number / bool
    question: str
    choices: tuple[Choice, ...]


YES_NO = (Choice("是", True), Choice("否", False))

FIELDS: tuple[FieldCard, ...] = (
    FieldCard("area_temperature_c", "number", "灾区温度（℃）：现场测定值是多少？", (Choice("30 ℃", 30.0), Choice("45 ℃", 45.0), Choice("60 ℃", 60.0))),
    FieldCard("visibility_m", "number", "能见度（米）：现场测定值是多少？", (Choice("20 米", 20.0), Choice("5 米", 5.0), Choice("0.5 米", 0.5))),
    FieldCard("fire_source_confirmed", "bool", "火源是否明确？", YES_NO),
    FieldCard("water_supply_sufficient", "bool", "水源、人力和物力是否充足？", YES_NO),
    FieldCard("return_airway_clear", "bool", "回风道是否畅通？", YES_NO),
)

CONFIRMERS = ("总指挥", "总工程师", "救护队带队指挥员")  # 占位，与动作分级表里封闭方案的确认人对应

NOTE = "按现场实际选择；表里没有的数值（温度、能见度）在“其他”里直接输入。选项只是常用档位，不是建议。"


def export() -> dict:
    return {
        "$comment": "由 rescue_contract.field_conditions 导出；占位值，待专家审定",
        "note": NOTE,
        "confirmers": list(CONFIRMERS),
        "fields": [
            {"field": f.field, "value_type": f.value_type, "question": f.question, "choices": [{"label": c.label, "value": c.value} for c in f.choices]}
            for f in FIELDS
        ],
    }
