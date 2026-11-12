"""红线规则引擎：对结构化处置方案做确定性校验。

这是红线规则的唯一实现。使用它的地方：
- actions 服务在执行每条需确认的指令前强制校验（最后一道防线，不依赖 DSH 配置）；
- actions 服务的 check_plan 工具和 /redlines/check 接口（合规审核 Agent 和 DSH 审批插件调用）。

设计原则：
- 每条规则对应《矿山救援规程》（应急管理部令第 16 号）的具体条款；
- 现场条件未知时按“不放行”处理（fail closed），并在说明里指出缺什么数据；
- 规则只做拦截，不做推荐；推荐是专业模型和 Agent 的事。

TODO(专家): 本矿防灭火设计和预案中的要求接入后在此补充规则，并由通风部门和救护队审定。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from pydantic import BaseModel

from .models import (
    ExplosionRisk,
    FireType,
    Plan,
    PlanKind,
    PlanStep,
    StepType,
)

SEAL_STEPS = {StepType.TEMPORARY_SEAL, StepType.PERMANENT_SEAL}
VENTILATION_CHANGE_STEPS = {
    StepType.REVERSE_VENTILATION,
    StepType.SHORT_CIRCUIT_VENTILATION,
    StepType.STOP_MAIN_FAN,
}


class Violation(BaseModel):
    rule_id: str
    article: str
    message: str
    step_order: int | None = None


@dataclass(frozen=True)
class Rule:
    rule_id: str
    article: str
    summary: str
    applies_to: frozenset[PlanKind]
    check: Callable[[Plan], list[Violation]]


def _steps(plan: Plan) -> list[PlanStep]:
    return sorted(plan.steps, key=lambda s: s.order)


def _before(plan: Plan, step: PlanStep, types: set[StepType]) -> bool:
    return any(s.type in types and s.order < step.order for s in plan.steps)


def _v(rule: str, article: str, message: str, step: PlanStep | None = None) -> Violation:
    return Violation(rule_id=rule, article=article, message=message, step_order=step.order if step else None)


# ── 规则 ────────────────────────────────────────────────────────────────────


def _rl077(plan: Plan) -> list[Violation]:
    out = []
    for s in _steps(plan):
        if s.type in {StepType.REVERSE_VENTILATION, StepType.SHORT_CIRCUIT_VENTILATION} and not _before(plan, s, {StepType.EVACUATE}):
            out.append(_v("RL-077", "第77条", "反风或风流短路前必须先撤出原进风侧和受影响区域人员，方案缺少前置撤人步骤。", s))
    return out


def _rl079(plan: Plan) -> list[Violation]:
    ctx = plan.context
    out = []
    for s in _steps(plan):
        if s.type != StepType.WATER_FIREFIGHTING:
            continue
        missing = []
        if ctx.fire_source_confirmed is not True:
            missing.append("火源明确")
        if ctx.water_supply_sufficient is not True:
            missing.append("水源、人力和物力充足")
        if ctx.return_airway_clear is not True:
            missing.append("回风道畅通")
        if ctx.methane_pct is None or ctx.methane_pct > 2.0:
            missing.append("甲烷浓度不超过2%")
        if missing:
            out.append(_v("RL-079", "第79条", "用水灭火的条件未满足或未确认：" + "、".join(missing) + "。", s))
    return out


def _rl084(plan: Plan) -> list[Violation]:
    out = []
    for s in _steps(plan):
        if s.type == StepType.PERMANENT_SEAL and not _before(plan, s, {StepType.TEMPORARY_SEAL}):
            out.append(_v("RL-084", "第84条", "封闭火区应先建临时密闭，确认无爆炸危险后再建永久或防爆密闭。", s))
    return out


def _rl085(plan: Plan) -> list[Violation]:
    if plan.context.explosion_risk == ExplosionRisk.NOT_EXPLOSIVE:
        return []
    out = []
    for s in _steps(plan):
        if s.type in SEAL_STEPS and not _before(plan, s, {StepType.INERT_INJECTION}):
            reason = "爆炸危险性未知" if plan.context.explosion_risk == ExplosionRisk.UNKNOWN else "火区存在爆炸危险"
            out.append(_v("RL-085", "第85条", f"{reason}，封闭前必须先注入惰性气体等抑爆，方案缺少前置注惰步骤。", s))
            break  # 只报第一处
    return out


def _rl098(plan: Plan) -> list[Violation]:
    if plan.context.fire_type != FireType.GOAF_SPONTANEOUS:
        return []
    return [
        _v("RL-098", "第98条", "采空区火灾应保持通风系统稳定，方案中不应包含反风、风流短路或停主要通风机。", s)
        for s in _steps(plan)
        if s.type in VENTILATION_CHANGE_STEPS
    ]


def _rl057(plan: Plan) -> list[Violation]:
    out = []
    for s in _steps(plan):
        if s.type not in {StepType.ENTER_DISASTER_AREA, StepType.INSPECT_SEALED_AREA}:
            continue
        if s.team_size is None or s.team_size < 6:
            out.append(_v("RL-057", "第57条", "进入灾区的救援小队不得少于6人（未填写人数也视为不满足）。", s))
        if s.standby_team is not True:
            out.append(_v("RL-053", "第53条", "执行灾区探察或救援任务时应设立待机小队。", s))
    return out


def _rl088(plan: Plan) -> list[Violation]:
    ctx = plan.context
    out = []
    for s in _steps(plan):
        if s.type not in {StepType.ENTER_DISASTER_AREA, StepType.DIRECT_FIREFIGHTING, StepType.WATER_FIREFIGHTING}:
            continue
        if ctx.area_temperature_c is None:
            out.append(_v("RL-088", "第88条", "缺少灾区温度数据，无法确认能否进入，按不放行处理。", s))
        elif ctx.area_temperature_c > 40:
            out.append(
                _v(
                    "RL-088",
                    "第88条",
                    f"灾区温度 {ctx.area_temperature_c}℃ 超过40℃，不得佩用氧气呼吸器作业（抢救人员时须严格限时，需人工另行决策）。",
                    s,
                )
            )
        # 能见度未知按不放行（2026-11-12 用户决定，与温度一致；原来未知时放行，不符合“未知按不通过”）
        if ctx.visibility_m is None:
            out.append(_v("RL-088", "第88条", "缺少能见度数据，无法确认能否进入，按不放行处理（经现场条件卡片补充）。", s))
        elif ctx.visibility_m < 1:
            out.append(_v("RL-088", "第88条", "能见度小于1米的巷道严禁进入。", s))
    return out


def _rl075(plan: Plan) -> list[Violation]:
    ctx = plan.context
    out = []
    for s in _steps(plan):
        if s.type not in {StepType.ENTER_DISASTER_AREA, StepType.DIRECT_FIREFIGHTING, StepType.WATER_FIREFIGHTING}:
            continue
        if ctx.methane_pct is None:
            out.append(_v("RL-075", "第75条", "缺少灾区甲烷浓度，无法判断是否需立即撤出，按不放行处理。", s))
        elif ctx.methane_pct > 2.0 and ctx.methane_rising is not False:
            out.append(_v("RL-075", "第75条", f"甲烷浓度 {ctx.methane_pct}% 超过2%且可能继续上升，应撤到安全地点，不得安排进入。", s))
    return out


def _rl087(plan: Plan) -> list[Violation]:
    ctx = plan.context
    out = []
    for s in _steps(plan):
        if s.type != StepType.INSPECT_SEALED_AREA:
            continue
        if ctx.sealed_at is None:
            out.append(_v("RL-087", "第87条", "缺少火区封闭时间，无法确认是否已满24小时。", s))
        elif ctx.as_of - ctx.sealed_at < timedelta(hours=24):
            out.append(_v("RL-087", "第87条", "火区封闭后进入检查或加固密闭应在24小时后进行。", s))
    return out


def _rl164(plan: Plan) -> list[Violation]:
    out = []
    for s in _steps(plan):
        if s.type != StepType.UNSEAL:
            continue
        lacking = []
        if not _before(plan, s, {StepType.MONITOR}):
            lacking.append("启封前检查温度、气体和支护")
        if not _before(plan, s, {StepType.POWER_CUTOFF}):
            lacking.append("切断回风流电源")
        if not _before(plan, s, {StepType.EVACUATE}):
            lacking.append("撤出回风侧人员")
        if lacking:
            out.append(_v("RL-164", "第164条", "启封前缺少必要步骤：" + "、".join(lacking) + "。", s))
    return out


ALL_PLANS = frozenset(PlanKind)

RULES: tuple[Rule, ...] = (
    Rule("RL-075", "第75条", "甲烷超过2%且上升、风量突变或风流逆转征兆时立即撤出", ALL_PLANS, _rl075),
    Rule("RL-077", "第77条", "反风或风流短路前先撤出受影响区域人员", ALL_PLANS, _rl077),
    Rule("RL-079", "第79条", "用水灭火须同时满足四个条件", ALL_PLANS, _rl079),
    Rule("RL-084", "第84条", "先建临时密闭，确认无爆炸危险后再建永久密闭", ALL_PLANS, _rl084),
    Rule("RL-085", "第85条", "封闭有爆炸危险的火区先注惰抑爆", ALL_PLANS, _rl085),
    Rule("RL-087", "第87条", "火区封闭后进入检查一般在24小时后", ALL_PLANS, _rl087),
    Rule("RL-088", "第88条", "高温浓烟环境的进入限制", ALL_PLANS, _rl088),
    Rule("RL-098", "第98条", "采空区火灾保持通风系统稳定", ALL_PLANS, _rl098),
    Rule("RL-057", "第57、53条", "进入灾区不少于6人并设待机小队", ALL_PLANS, _rl057),
    Rule("RL-164", "第164条", "启封前检查、断电、撤人", frozenset({PlanKind.UNSEALING}), _rl164),
)


def check_plan(plan: Plan) -> list[Violation]:
    """返回全部违规项；空列表表示通过红线校验（不代表方案正确，只代表没有触碰红线）。"""
    violations: list[Violation] = []
    for rule in RULES:
        if plan.kind in rule.applies_to:
            violations.extend(rule.check(plan))
    return sorted(violations, key=lambda v: (v.step_order or 0, v.rule_id))


def format_violations(violations: list[Violation]) -> str:
    lines = [f"红线校验未通过（{len(violations)} 项）："]
    for v in violations:
        where = f"步骤{v.step_order}：" if v.step_order else ""
        lines.append(f"- [{v.article}] {where}{v.message}")
    return "\n".join(lines)
