"""指令发布前的校验：红线 + 方案类型 + 数据时效 + 记录核对。

guard 服务（执行前强制）和 DSH 审批插件的预检接口共用这里的逻辑。指令引用证据插件写的结果记录（record_id），
红线引擎的输入来自计算，不来自模型填写：预警、升级、封闭方案都要按记录核对。
now 为 None 时不检查时效（只用于自查）；发布和审批预检必须传入矿井时间，取不到时由调用方拒绝（fail closed）。

记录核对：记录存在、内容哈希没变、是规定技能和脚本的结果、时效、工作面，再按记录内容核对等级或升级结论。
封闭方案只提交审核记录（plan-compliance/review）的编号：方案从记录里取，不经模型转抄；核对审核结论为通过、
方案与上游的方案记录（sealing-plan/compose）一致，再做方案类型、时效和红线校验。
预检结果附 ApprovalSummary（审批弹窗的结构化内容）。

第三版 P2：第一版的研判快照（snapshot_id）、防灭火方案和启封方案的草案核对已删除——它们依赖第一版 workflow 的草案，
没有对应的第二版流程；需要时随新流程按记录重做（docs/design-v3.md）。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .action_policy import ACTION_NAMES
from .models import (
    ApprovalCheck,
    ApprovalSummary,
    Escalation,
    Plan,
    PlanKind,
    Record,
    RecordKind,
    WarningLevel,
    WarningNotice,
)
from .records import RecordNotFound, RecordStore, RecordTampered
from .redlines import check_plan, format_violations

CONTEXT_MAX_AGE = timedelta(hours=2)  # 方案依据的现场条件不得早于 2 小时。TODO(专家): 确认时效要求

PLAN_TOOLS: dict[str, PlanKind] = {
    "issue_sealing_plan": PlanKind.SEALING,
    "issue_firefighting_plan": PlanKind.FIREFIGHTING,
    "approve_unsealing": PlanKind.UNSEALING,
}
NAMES = {"none": "无", "blue": "蓝色", "yellow": "黄色", "orange": "橙色", "red": "红色"}


def _age_problems(as_of: datetime, now: datetime | None, what: str) -> list[str]:
    if now is None:
        return []
    if as_of > now + timedelta(minutes=5):
        return [f"{what}晚于矿井当前时间"]
    if now - as_of > CONTEXT_MAX_AGE:
        return [f"{what}已过时（{as_of:%m-%d %H:%M}），需用最新数据重新研判"]
    return []


def validate_plan(tool: str, plan: Plan, now: datetime | None) -> list[str]:
    """返回全部问题；空列表表示可以发布。now 为 None 时不检查时效（自查场景）。"""
    problems = []
    expected = PLAN_TOOLS.get(tool)
    if expected and plan.kind != expected:
        problems.append(f"方案类型应为 {expected.value}，实际为 {plan.kind.value}")
    problems += _age_problems(plan.context.as_of, now, "现场条件")
    violations = check_plan(plan)
    if violations:
        problems.append(format_violations(violations))
    return problems


# ── 按记录核对 ──────────────────────────────────────────────────────────────

# 指令 → 它引用的记录必须是哪个技能、哪个脚本的结果
RECORD_SOURCES: dict[str, tuple[str, str]] = {
    "publish_warning": ("fire-assessment", "warning"),
    "start_monitoring": ("fire-assessment", "warning"),  # 观察跟进：引用同一个预警等级记录，不手写等级
    "escalate_emergency": ("emergency-escalation", "escalation"),
    "issue_sealing_plan": ("plan-compliance", "review"),
    "issue_firefighting_plan": ("plan-compliance", "review"),
    # 启封评估的结果记录本身就是方案记录（没有模型编写和审核阶段：方案由代码按固定步骤生成，红线在这里和 guard 强制校验）
    "approve_unsealing": ("unsealing-plan", "unsealing_plan"),
}
# 审核记录的输入里，方案记录必须是这个技能、这个脚本的结果（按工具区分：封闭方案、防灭火方案各出自自己的技能）
PLAN_RECORD_SOURCES: dict[str, tuple[str, str]] = {
    "issue_sealing_plan": ("sealing-plan", "compose"),
    "issue_firefighting_plan": ("firefighting-plan", "compose"),
}


def _record(records: RecordStore | None, record_id: str, tool: str, what: str, now: datetime | None) -> tuple[Record | None, list[ApprovalCheck]]:
    """取记录并逐项核对，返回（记录, 核对项）。任一项不通过时记录为 None。"""
    checks: list[ApprovalCheck] = []

    def fail(name: str, detail: str) -> tuple[None, list[ApprovalCheck]]:
        checks.append(ApprovalCheck(name=name, ok=False, detail=detail))
        return None, checks

    if records is None:
        return fail("引用记录", "guard 没有配置记录目录，不能核对记录（fail closed）")
    try:
        rec = records.get(record_id)
    except RecordNotFound:
        return fail("引用记录", f"找不到记录 {record_id}：record_id 必须照抄固定流程返回的 record_ids")
    except RecordTampered as exc:
        return fail("记录完整性", str(exc))
    except ValueError as exc:  # 记录文件不合契约
        return fail("记录完整性", f"记录 {record_id} 格式不对：{exc}")
    skill, script = RECORD_SOURCES[tool]
    got = (rec.source.get("skill"), rec.source.get("script"))
    if rec.kind != RecordKind.RESULT or got != (skill, script):
        return fail("记录类型", f"记录 {record_id} 是 {rec.kind.value}（{got[0]}/{got[1]}），{what}需要 {skill}/{script} 的结果记录")
    checks.append(ApprovalCheck(name="引用记录", ok=True, detail=f"{record_id}（{skill}/{script}，矿井时间 {rec.as_of:%m-%d %H:%M}）"))
    stale = _age_problems(rec.as_of, now, f"{what}引用的记录 {record_id}")
    if stale:
        return fail("记录时效", stale[0])
    if now is not None:
        checks.append(ApprovalCheck(name="记录时效", ok=True, detail=f"不早于矿井时间 {now:%m-%d %H:%M} 前 {int(CONTEXT_MAX_AGE.total_seconds() // 3600)} 小时"))
    return rec, checks


def _result(rec: Record) -> dict[str, Any]:
    content = rec.content if isinstance(rec.content, dict) else {}
    return content.get("result") or {}


def check_warning_record(
    notice: WarningNotice, records: RecordStore | None, now: datetime | None
) -> tuple[list[str], list[str], list[ApprovalCheck]]:
    """（问题, 给确认人看的说明, 核对项）。"""
    rec, checks = _record(records, notice.record_id or "", "publish_warning", "预警", now)
    if rec is None:
        return [c.detail or c.name for c in checks if not c.ok], [], checks
    r = _result(rec)
    if rec.panel and not mentions_panel(notice.location, rec.panel):
        checks.append(ApprovalCheck(name="工作面", ok=False, detail=f"预警位置“{notice.location}”与记录的工作面 {rec.panel} 不符"))
        return [checks[-1].detail or ""], [], checks
    computed = r.get("level")
    if computed not in NAMES:
        checks.append(ApprovalCheck(name="预警等级", ok=False, detail=f"记录 {rec.record_id} 里没有可核对的预警等级"))
        return [checks[-1].detail or ""], [], checks
    if notice.level.value == computed:
        checks.append(ApprovalCheck(name="预警等级", ok=True, detail=f"与计算结果一致（{NAMES[computed]}）"))
        return [], [], checks
    order = list(NAMES)  # none → red
    if order.index(notice.level.value) < order.index(computed):
        # 只能往高调（2026-11-12 用户决定）：等级偏高多半是测点故障，应先核实测点、修正数据再重新研判，不能直接把结论改低
        msg = (
            f"预警等级不能低于记录 {rec.record_id} 的计算结果（计算 {NAMES[computed]}，提交 {NAMES[notice.level.value]}）："
            "人工调整只能往高调。认为等级偏高时，先现场核实测点（疑似故障、卡值）再重新研判；系统暂不支持人工降级"
        )
        checks.append(ApprovalCheck(name="预警等级", ok=False, detail=msg))
        return [msg], [], checks
    if not (notice.override_reason or "").strip():
        msg = f"预警等级 {NAMES[notice.level.value]} 与记录 {rec.record_id} 的计算结果 {NAMES[computed]} 不一致；人工改等级须在 override_reason 写明理由"
        checks.append(ApprovalCheck(name="预警等级", ok=False, detail=msg))
        return [msg], [], checks
    note = f"与计算结果不一致：计算 {NAMES[computed]}，提交 {NAMES[notice.level.value]}，理由：{notice.override_reason}"
    checks.append(ApprovalCheck(name="预警等级", ok=True, detail=note))
    return [], [note], checks


def mentions_panel(text: str, panel: str) -> bool:
    """text 里写了这个工作面编号（前后不紧挨字母或数字）：只按子串判断时，"1201" 会被当成写了 "120"、"201"。"""
    return re.search(rf"(?<![0-9A-Za-z]){re.escape(panel)}(?![0-9A-Za-z])", text) is not None


def warning_message(notice: WarningNotice, result: dict[str, Any], record_id: str) -> str:
    """发到矿井的预警正文：由代码按预警等级记录生成，不用智能体写的 summary。确认弹窗显示的就是这段文字。
    人工改等级时写明计算结果和理由（理由是人工给的，原样写入）。"""
    computed = str(result.get("level") or "")
    submitted = notice.level.value
    if submitted == computed:
        name = str(result.get("level_name") or NAMES.get(submitted, submitted))
        head = f"{notice.location}：{name}预警"
        triggered = [str(t).removeprefix(f"{name}：") for t in result.get("triggered") or []]  # 触发条件自带“等级：”前缀，正文里不重复
        body = [f"触发条件：{'；'.join(triggered)}"] if triggered else []
    else:
        head = f"{notice.location}：{NAMES.get(submitted, submitted)}预警"
        body = [f"人工调整等级，计算结果为{result.get('level_name') or NAMES.get(computed, computed)}，理由：{(notice.override_reason or '').strip()}"]
    return "。".join([head, *body, f"依据研判记录 {record_id}"]) + "。"


def escalation_message(result: dict[str, Any], record_id: str) -> str:
    """发到矿井的升级建议正文：由代码按升级建议记录生成（理由、规程依据），不用智能体写的 reason、recommended_level。"""
    panel = str(result.get("panel") or "").strip()
    parts = [f"{panel + ' 工作面' if panel else '本工作面'}建议升级为应急响应，由指挥人员决定"]
    if reasons := [str(x) for x in result.get("reasons") or []]:
        parts.append(f"理由：{'；'.join(reasons)}")
    if arts := [str(a.get("article")) for a in result.get("articles") or [] if isinstance(a, dict) and a.get("article")]:
        parts.append(f"规程依据：{'、'.join(arts)}")
    parts.append(f"依据升级建议记录 {record_id}")
    return "。".join(parts) + "。"


def check_monitoring_record(record_id: str, records: RecordStore | None, now: datetime | None) -> tuple[list[str], list[ApprovalCheck]]:
    """加密监测（观察跟进）：引用 run_fire_assessment 的预警等级记录，记录要新鲜、有可核对的等级；监测只增加观察，不改变现场。"""
    rec, checks = _record(records, record_id, "start_monitoring", "加密监测", now)
    if rec is None:
        return [c.detail or c.name for c in checks if not c.ok], checks
    level = _result(rec).get("level")
    if level not in NAMES:
        checks.append(ApprovalCheck(name="预警等级", ok=False, detail=f"记录 {rec.record_id} 里没有可核对的预警等级"))
        return [checks[-1].detail or ""], checks
    checks.append(ApprovalCheck(name="预警等级", ok=True, detail=f"{rec.panel or '工作面'} 当前 {NAMES[level]}，按记录开始加密监测"))
    return [], checks


def check_escalation_record(esc: Escalation, records: RecordStore | None, now: datetime | None) -> tuple[list[str], list[ApprovalCheck]]:
    rec, checks = _record(records, esc.record_id or "", "escalate_emergency", "升级", now)
    if rec is None:
        return [c.detail or c.name for c in checks if not c.ok], checks
    if _result(rec).get("recommend") is not True:
        msg = f"升级建议记录 {rec.record_id} 的结论是不建议升级；需要升级时由指挥人员另行决定，不经本工具提交"
        checks.append(ApprovalCheck(name="升级建议", ok=False, detail=msg))
        return [msg], checks
    checks.append(ApprovalCheck(name="升级建议", ok=True, detail="记录的结论为建议升级"))
    return [], checks


def unseal_from_record(
    tool: str, record_id: str, records: RecordStore | None, now: datetime | None
) -> tuple[Plan | None, list[str], list[str], list[ApprovalCheck]]:
    """启封审批：按启封评估记录（unsealing-plan/unsealing_plan）取方案并核对。结论必须是“具备启封条件”，方案类型、时效、红线
    （RL-164：先检查、断电、撤人）与下发时的强制校验同一套。返回（方案, 问题, 给确认人看的说明, 核对项）；有问题时方案为 None。"""
    rec, checks = _record(records, record_id, tool, "启封方案", now)
    if rec is None:
        return None, [c.detail or c.name for c in checks if not c.ok], [], checks

    def fail(name: str, detail: str) -> tuple[None, list[str], list[str], list[ApprovalCheck]]:
        checks.append(ApprovalCheck(name=name, ok=False, detail=detail))
        return None, [c.detail or c.name for c in checks if not c.ok], [], checks

    r = _result(rec)
    if r.get("ready") is not True or not r.get("plan"):
        return fail("启封条件", f"启封评估记录 {rec.record_id} 的结论是{r.get('ready_name') or '不具备启封条件'}（不满足：{r.get('unmet_labels') or '未知'}），不能提交启封审批")
    checks.append(ApprovalCheck(name="启封条件", ok=True, detail=f"评估记录 {rec.record_id} 的结论为具备启封条件（{r.get('met_count')} 项判定全部满足）"))
    try:
        plan = Plan.model_validate({**r["plan"], "record_id": rec.record_id})
    except ValueError as exc:
        return fail("方案格式", f"记录里的方案不符合数据契约：{exc}")
    if rec.panel and plan.panel != rec.panel:
        return fail("工作面", f"方案的工作面 {plan.panel} 与记录的工作面 {rec.panel} 不符")
    problems = validate_plan(tool, plan, now)
    checks.append(ApprovalCheck(name="方案类型与时效红线", ok=not problems, detail="；".join(problems) or "通过（与下发时的强制校验同一套规则）"))
    if problems:
        return None, problems, [], checks
    return plan, [], ["启封是不可逆的现场动作：请确认现场已具备启封的组织条件（救护队、通风、断电、撤人）"], checks


def plan_from_record(
    tool: str, record_id: str, records: RecordStore | None, now: datetime | None
) -> tuple[Plan | None, list[str], list[str], list[ApprovalCheck]]:
    """第二版：按审核记录取方案并核对。返回（方案, 问题, 给确认人看的说明, 核对项）；有问题时方案为 None。"""
    if tool == "approve_unsealing":
        return unseal_from_record(tool, record_id, records, now)
    if tool not in PLAN_TOOLS:
        msg = f"{tool} 不支持按记录下发（目前有 issue_sealing_plan、issue_firefighting_plan、approve_unsealing）"
        return None, [msg], [], [ApprovalCheck(name="引用记录", ok=False, detail=msg)]
    rec, checks = _record(records, record_id, tool, "方案", now)

    def fail(name: str, detail: str) -> tuple[None, list[str], list[str], list[ApprovalCheck]]:
        checks.append(ApprovalCheck(name=name, ok=False, detail=detail))
        return None, [c.detail or c.name for c in checks if not c.ok], [], checks

    if rec is None or records is None:
        return None, [c.detail or c.name for c in checks if not c.ok], [], checks
    r = _result(rec)
    if r.get("verdict") != "pass":
        return fail("合规审核", f"审核记录 {rec.record_id} 的结论是{r.get('verdict_name') or '不通过'}，不能下发；按审核意见修改后重新编制")
    checks.append(ApprovalCheck(name="合规审核", ok=True, detail=f"审核记录 {rec.record_id} 的结论为通过"))
    parent = (rec.source.get("inputs") or {}).get("plan")
    try:
        prec = records.get(str(parent))
    except (RecordNotFound, RecordTampered, ValueError) as exc:
        return fail("方案记录", f"审核记录 {rec.record_id} 引用的方案记录 {parent} 读不出来：{exc}")
    source = PLAN_RECORD_SOURCES[tool]
    if (prec.source.get("skill"), prec.source.get("script")) != source or prec.kind != RecordKind.RESULT:
        return fail("方案记录", f"审核记录 {rec.record_id} 引用的 {parent} 不是 {'/'.join(source)} 的结果记录")
    if _result(prec).get("plan") != r.get("plan"):
        return fail("方案记录", f"审核记录 {rec.record_id} 里的方案与方案记录 {parent} 不一致")
    checks.append(ApprovalCheck(name="方案记录", ok=True, detail=f"{parent}（{'/'.join(source)}）与审核记录一致"))
    try:
        plan = Plan.model_validate({**(r.get("plan") or {}), "record_id": rec.record_id})
    except ValueError as exc:
        return fail("方案格式", f"记录里的方案不符合数据契约：{exc}")
    if rec.panel and plan.panel != rec.panel:
        return fail("工作面", f"方案的工作面 {plan.panel} 与记录的工作面 {rec.panel} 不符")
    problems = validate_plan(tool, plan, now)
    checks.append(ApprovalCheck(name="方案类型与时效红线", ok=not problems, detail="；".join(problems) or "通过（与下发时的强制校验同一套规则）"))
    if problems:
        return None, problems, [], checks
    return plan, [], [str(x) for x in r.get("human_items") or []] + missing_items(_result(prec), r.get("human_items") or []), checks


def missing_items(plan_result: dict[str, Any], already: list[Any]) -> list[str]:
    """方案缺的输入逐条列为“请确认人把关”（2026-11-12 用户决定：缺现场条件时方案仍能下发，但缺什么要逐条摆在确认人面前）。
    合规审核已经列过的（按名称开头）不重复。"""
    out = []
    for m in plan_result.get("missing_inputs") or []:
        if not isinstance(m, dict):
            continue
        label = str(m.get("label") or m.get("field") or "")
        if not label or any(str(x).startswith(label) for x in already):
            continue
        if m.get("kind") == "human":
            out.append(f"{label}：未经现场条件卡片提供，请确认已掌握现场情况")
        else:
            out.append(f"{label}：{m.get('note') or '缺失'}")
    return out


def evacuation_from_record(notice: dict[str, Any], records: RecordStore | None) -> dict[str, Any]:
    """撤人名单留空、给了人员记录（run_fire_assessment 的 record_ids.personnel）时，按记录填受威胁人员和可达的避灾路线。
    名单已给出，或读不到记录、记录没有算出受威胁人员时原样返回（之后的核对会拦下空名单）。"""
    if notice.get("persons") or not notice.get("record_id") or records is None:
        return notice
    try:
        r = _result(records.get(str(notice["record_id"])))
    except (RecordNotFound, RecordTampered, ValueError):
        return notice
    persons: list[str] = []
    routes: dict[str, list[str]] = dict(notice.get("routes") or {})
    for g in r.get("threatened") or []:
        route = g.get("escape_route") or {}
        for pid in g.get("persons") or []:
            if pid not in persons:
                persons.append(str(pid))
            if route.get("reachable") and route.get("nodes") and pid not in routes:
                routes[str(pid)] = [str(n) for n in route["nodes"]]
    if not persons:
        return notice
    return {**notice, "persons": persons, "routes": routes, "reason": evacuation_reason(r, persons, routes, str(notice["record_id"]))}


def evacuation_reason(result: dict[str, Any], persons: list[str], routes: dict[str, list[str]], record_id: str) -> str:
    """按人员记录生成撤人通知正文（不用智能体写的文字）：人数、去向、走不通的人，以及“补充、不替代预案撤人”。"""
    panel = str(result.get("panel") or "").strip()
    head = f"{panel} 工作面" if panel else "受影响区域"
    targets = []
    for g in result.get("threatened") or []:
        route = g.get("escape_route") or {}
        if route.get("reachable") and (name := route.get("target_name") or route.get("target")) and name not in targets:
            targets.append(str(name))
    blocked = [p for p in persons if p not in routes]
    parts = [f"{head}补充撤人名单 {len(persons)} 人" + (f"，撤往 {'、'.join(targets)}" if targets else "")]
    if blocked:
        parts.append(f"其中 {len(blocked)} 人（{'、'.join(blocked)}）避开受污染巷道后没有可达避灾路线，需人工研判")
    parts.append(f"本通知是预案撤人的补充，不替代、不延迟预案撤人；依据人员记录 {record_id}")
    return "；".join(parts) + "。"


def dispatch_from_record(order: dict[str, Any], records: RecordStore | None) -> dict[str, Any]:
    """调度单明细留空、给了方案的审核记录（或方案记录）时，按方案记录的 dispatch_lines 填编号、用途和明细。
    明细已给出，或读不到记录、方案没有需调运的物资时原样返回（之后的核对会拦下空明细）。"""
    if order.get("lines") or not order.get("record_id") or records is None:
        return order
    try:
        rec = records.get(str(order["record_id"]))
        pr = _result(rec)
        if "dispatch_lines" not in pr:  # 审核记录：方案在它的输入里
            pr = _result(records.get(str((rec.source.get("inputs") or {}).get("plan"))))
    except (RecordNotFound, RecordTampered, ValueError, KeyError):
        return order
    lines = [{k: ln[k] for k in ("item_type", "quantity", "unit", "from_node", "to_node")} for ln in pr.get("dispatch_lines") or []]
    if not lines:
        return order
    plan = pr.get("plan") or {}
    return {
        **order,
        "order_id": order.get("order_id") or f"DO-{plan.get('plan_id', '')}",
        "purpose": order.get("purpose") or f"{plan.get('title', '方案')}（{plan.get('plan_id', '')}）物资调运",
        "lines": lines,
    }


def _show(v: Any) -> str:
    """人工输入值的显示：布尔写“是 / 否”，整数值的浮点数去掉“.0”。"""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _facts(tool: str, arguments: dict[str, Any], records: RecordStore | None) -> list[str]:
    """审批弹窗的要点：确认人要批的到底是什么。全部由代码从记录（或撤人、调度的指令参数）取，不经模型改写；
    取不到就不给（要点只是给人看的摘要，核对仍按 checks）。"""
    body = next((v for v in arguments.values() if isinstance(v, dict)), arguments) if arguments else {}
    try:
        if tool in ("publish_warning", "start_monitoring", "escalate_emergency"):
            rid = body.get("record_id") or arguments.get("record_id")
            if records is None or not isinstance(rid, str):
                return []
            r = _result(records.get(rid))
            # 预警、升级：弹窗显示的就是发到矿井的正文（与 guard 发出时同一个函数生成）
            if tool == "escalate_emergency":
                return [f"发出正文：{escalation_message(r, rid)}"]
            if tool == "publish_warning":
                return [f"发出正文：{warning_message(WarningNotice.model_validate(body), r, rid)}"]
            out = [f"预警等级：{r['level_name']}"] if r.get("level_name") else []
            return out + [f"触发条件：{t}" for t in r.get("triggered") or []]
        if tool in PLAN_TOOLS:
            rid = arguments.get("record_id")
            if records is None or not isinstance(rid, str):
                return []
            rec = records.get(rid)
            if tool == "approve_unsealing":
                plan = _result(rec).get("plan") or {}
                return [f"步骤共 {len(plan.get('steps') or [])} 步"] if plan.get("steps") else []
            pr = _result(records.get(str((rec.source.get("inputs") or {}).get("plan"))))
            out = []
            steps = [str(s.get("type_name") or s.get("type")) for s in pr.get("steps_view") or []]
            if steps:
                out.append(f"步骤：{' → '.join(steps)}")
            if pr.get("seal_walls_text"):
                out.append(f"密闭巷道：{pr['seal_walls_text']}")
            short = [str(s.get("label") or s.get("item_type")) for s in pr.get("shortages") or []]
            out.append(f"物资缺口 {len(short)} 项：{'、'.join(short)}" if short else "物资：台账无缺口")
            # 人工输入的现场条件（来自现场条件卡片）：写明值和确认人，让确认人看到哪些条件是人给的、谁确认的
            given = [f"{h.get('label') or h.get('field')} {_show(h.get('value'))}（确认人 {h.get('confirmed_by')}）" for h in pr.get("human_inputs") or [] if isinstance(h, dict)]
            if given:
                out.append(f"人工确认的现场条件：{'；'.join(given)}")
            # 缺的输入不在要点里写：逐条列在“请确认人把关”（missing_items）
            return out
        if tool == "notify_evacuation":
            persons = [str(p) for p in body.get("persons") or []]
            routes = {str(k): v for k, v in (body.get("routes") or {}).items() if isinstance(v, list) and v}
            # 去向的中文名从引用的人员记录取（避难硐室、出口）；取不到就用节点编号（2026-11-11 演示：弹窗只写了“撤往：N8”）
            names: dict[str, str] = {}
            if body.get("record_id") and records is not None:
                try:
                    for g in _result(records.get(str(body["record_id"]))).get("threatened") or []:
                        r = g.get("escape_route") or {}
                        if r.get("target") and r.get("target_name"):
                            names[str(r["target"])] = str(r["target_name"])
                except (RecordNotFound, RecordTampered, ValueError):
                    pass
            dest = sorted({str(v[-1]) for v in routes.values()})
            out = [f"补充撤人名单 {len(persons)} 人"]
            if dest:
                out.append(f"撤往：{'、'.join(f'{names[d]}（{d}）' if d in names else d for d in dest)}")
            # 没有可达避灾路线的人要让确认人看到（名单里有人、却没有路线）
            stranded = [p for p in persons if p not in routes]
            if stranded and routes:
                out.append(f"没有可达避灾路线 {len(stranded)} 人：{'、'.join(stranded)}（需人工研判）")
            return out
        if tool == "issue_dispatch_order":
            # 引用了方案记录：物资和地点的中文名从方案的“需调运”明细取（与明细逐条对应）；没有就用编号
            named: dict[tuple, dict[str, Any]] = {}
            if body.get("record_id") and records is not None:
                try:
                    rec = records.get(str(body["record_id"]))
                    pr = _result(rec)
                    if "dispatch_lines" not in pr:
                        pr = _result(records.get(str((rec.source.get("inputs") or {}).get("plan"))))
                    named = {(x["item_type"], x["from_node"], x["to_node"]): x for x in pr.get("dispatch_lines") or []}
                except (RecordNotFound, RecordTampered, ValueError, KeyError):
                    named = {}
            out = []
            for ln in body.get("lines") or []:
                if not isinstance(ln, dict):
                    continue
                n = named.get((ln.get("item_type"), ln.get("from_node"), ln.get("to_node"))) or {}
                label = str(n.get("label") or ln.get("item_type")).split("（")[0]
                frm = f"{n['from_name']}（{ln.get('from_node')}）" if n.get("from_name") else ln.get("from_node")
                to = f"{n['to_name']}（{ln.get('to_node')}）" if n.get("to_name") else ln.get("to_node")
                out.append(f"调运：{label} {ln.get('quantity')} {ln.get('unit')}（{frm} → {to}）")
            return out
    except (RecordNotFound, RecordTampered, ValueError, KeyError, TypeError, AttributeError):
        return []
    return []


def _summary(
    tool: str, arguments: dict[str, Any], problems: list[str], notes: list[str], checks: list[ApprovalCheck], action: str | None = None,
    records: RecordStore | None = None,
) -> dict[str, Any]:  # fmt: skip
    """审批弹窗的结构化内容（ApprovalSummary）：要做什么、引用哪些记录、核对结果、需要确认人把关的事项。"""
    body = next((v for v in arguments.values() if isinstance(v, dict)), arguments) if arguments else {}
    action = action or ACTION_NAMES.get(tool, tool)
    if tool == "publish_warning" and body.get("location"):
        action = f"发布 {body['location']} {NAMES.get(str(body.get('level')), body.get('level'))}预警"
    if not checks:  # 没有逐项核对的指令（撤人名单、调度单）：把预检结论作为一项
        checks = [ApprovalCheck(name="预检", ok=not problems, detail="；".join(problems) or None)]
    ids = [x for x in (body.get("record_id"),) if isinstance(x, str) and x]
    return ApprovalSummary(
        action=action, tool=tool, facts=_facts(tool, arguments, records) if not problems else [], record_ids=ids, checks=checks, human_items=notes,
        override_reason=body.get("override_reason") if isinstance(body.get("override_reason"), str) else None,
    ).model_dump(mode="json")  # fmt: skip


def precheck(tool: str, arguments: dict[str, Any], now: datetime | None, records: RecordStore | None = None) -> dict[str, Any]:
    """arguments 为工具调用参数。返回 {ok, problems, notes, summary}；notes 给确认人看（如人工改等级的理由），
    summary 是审批弹窗的结构化内容（ApprovalSummary）。预警、升级、封闭方案都按记录（record_id）核对。"""
    notes: list[str] = []
    checks: list[ApprovalCheck] = []
    action: str | None = None
    try:
        if tool == "publish_warning":
            n = WarningNotice.model_validate(arguments.get("notice", arguments))
            problems, notes, checks = check_warning_record(n, records, now)
        elif tool == "start_monitoring":
            record_id = arguments.get("record_id")
            if not isinstance(record_id, str) or not record_id:
                problems = ["缺少 record_id：加密监测只提交 run_fire_assessment 返回的 record_ids.warning"]
            else:
                problems, checks = check_monitoring_record(record_id, records, now)
        elif tool == "escalate_emergency":
            e = Escalation.model_validate(arguments.get("escalation", arguments))
            problems, checks = check_escalation_record(e, records, now)
        elif tool in PLAN_TOOLS:
            record_id = arguments.get("record_id")
            if not isinstance(record_id, str) or not record_id:
                problems = ["缺少 record_id：封闭方案、防灭火方案只提交审核记录编号（draft_sealing_plan、draft_firefighting_plan 返回的 record_ids.review），启封只提交启封评估记录编号（assess_unsealing 返回的 record_ids.unseal），方案由 guard 从记录取"]
            else:
                plan, problems, notes, checks = plan_from_record(tool, record_id, records, now)
                if plan is not None:
                    verb = "批准启封" if tool == "approve_unsealing" else "下发"  # 启封是不可逆的现场动作，标题不能写成“下发”
                    action = f"{verb} {plan.title}（{plan.plan_id}）"
        elif tool in ("notify_evacuation", "issue_dispatch_order"):
            problems = []  # 撤人名单、调度单：内容核对在 guard 里，按矿井数据（人员定位、网络、物资台账）
        else:
            # 没有核对规则的指令按不通过处理（fail closed）：新增 C 级工具时要在这里加它的核对，否则弹窗会写“预检已通过”却什么都没核对
            problems = [f"预检没有 {tool} 的核对规则，按不通过处理；新增对外动作要在 rescue_contract.validation.precheck 里加核对"]
    except Exception as exc:
        problems = [f"指令格式不符合数据契约：{exc}"]
        return {"ok": False, "problems": problems, "notes": [], "summary": _summary(tool, {}, problems, [], [])}
    return {"ok": not problems, "problems": problems, "notes": notes, "summary": _summary(tool, arguments, problems, notes, checks, action, records)}


__all__ = [
    "ACTION_NAMES",
    "CONTEXT_MAX_AGE",
    "PLAN_TOOLS",
    "RECORD_SOURCES",
    "WarningLevel",
    "check_escalation_record",
    "check_monitoring_record",
    "check_warning_record",
    "dispatch_from_record",
    "evacuation_from_record",
    "escalation_message",
    "evacuation_reason",
    "mentions_panel",
    "plan_from_record",
    "precheck",
    "validate_plan",
    "warning_message",
]
