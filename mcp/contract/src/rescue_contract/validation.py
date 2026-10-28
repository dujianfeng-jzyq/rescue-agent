"""指令发布前的校验：红线 + 方案类型 + 数据时效。

actions 服务（强制执行）、DSH 审批插件的预检接口、kb 服务的自查工具共用这里的逻辑。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .models import Plan, PlanKind
from .redlines import check_plan, format_violations

CONTEXT_MAX_AGE = timedelta(hours=2)  # 方案依据的现场条件不得早于 2 小时。TODO(专家): 确认时效要求

PLAN_TOOLS: dict[str, PlanKind] = {
    "issue_firefighting_plan": PlanKind.FIREFIGHTING,
    "issue_sealing_plan": PlanKind.SEALING,
    "approve_unsealing": PlanKind.UNSEALING,
}


def validate_plan(tool: str, plan: Plan, now: datetime | None) -> list[str]:
    """返回全部问题；空列表表示可以发布。now 为 None 时不检查时效（自查场景）。"""
    problems = []
    expected = PLAN_TOOLS.get(tool)
    if expected and plan.kind != expected:
        problems.append(f"方案类型应为 {expected.value}，实际为 {plan.kind.value}")
    if now is not None:
        if plan.context.as_of > now + timedelta(minutes=5):
            problems.append("现场条件时间晚于矿井当前时间")
        elif now - plan.context.as_of > CONTEXT_MAX_AGE:
            problems.append(f"现场条件已过时（{plan.context.as_of:%m-%d %H:%M}），需用最新数据重新研判")
    violations = check_plan(plan)
    if violations:
        problems.append(format_violations(violations))
    return problems


def precheck(tool: str, arguments: dict[str, Any], now: datetime | None) -> dict[str, Any]:
    """非方案类工具直接通过。arguments 为工具调用参数（含 plan 字段）。"""
    if tool not in PLAN_TOOLS:
        return {"ok": True, "problems": []}
    try:
        plan = Plan.model_validate(arguments.get("plan", arguments))
    except Exception as exc:
        return {"ok": False, "problems": [f"方案格式不符合数据契约：{exc}"]}
    problems = validate_plan(tool, plan, now)
    return {"ok": not problems, "problems": problems}
