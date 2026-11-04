"""指令发布前的校验：红线 + 方案类型 + 数据时效 + 研判快照（第一版）或记录（第二版）核对。

actions 服务（强制执行）、DSH 审批插件的预检接口、kb 服务的自查工具共用这里的逻辑。
快照核对（docs/workflow-design.md W6）：方案的现场条件和必备步骤、预警等级、升级建议、调度单，
都要与 workflow 服务生成的研判快照一致——红线引擎的输入来自计算，不来自模型填写。
store 为 None 时不做快照核对（只用于单元测试红线和时效逻辑）。
now 为 None 时不检查时效（只用于自查）；发布和审批预检必须传入矿井时间，取不到时由调用方拒绝（fail closed）。

第二版（docs/design-v2.md 4.8）：预警、升级引用证据插件写的结果记录（record_id）。核对：记录存在、内容哈希没变、
是规定技能和脚本的结果、时效、工作面，再按记录内容核对等级或升级结论。迁移完成前 snapshot_id 仍可用，二者只能给一个。
预检结果附 ApprovalSummary（审批弹窗的结构化内容）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .models import (
    HUMAN_INPUT_LABELS,
    ApprovalCheck,
    ApprovalSummary,
    DispatchOrder,
    Escalation,
    Plan,
    PlanKind,
    Record,
    RecordKind,
    Snapshot,
    SnapshotKind,
    WarningLevel,
    WarningNotice,
)
from .records import RecordNotFound, RecordStore, RecordTampered
from .redlines import check_plan, format_violations
from .snapshots import SnapshotNotFound, SnapshotStore

CONTEXT_MAX_AGE = timedelta(hours=2)  # 方案依据的现场条件不得早于 2 小时。TODO(专家): 确认时效要求

PLAN_TOOLS: dict[str, PlanKind] = {
    "issue_firefighting_plan": PlanKind.FIREFIGHTING,
    "issue_sealing_plan": PlanKind.SEALING,
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


def _snapshot(
    store: SnapshotStore, snapshot_id: str | None, kind: SnapshotKind, what: str, now: datetime | None = None
) -> tuple[Snapshot | None, list[str]]:
    """按编号取快照并核对类型；传入 now 时同时核对时效（快照的矿井时间不得早于 CONTEXT_MAX_AGE）。"""
    if not snapshot_id:
        return None, [f"缺少 snapshot_id：{what}必须引用 workflow 生成的快照（kind={kind.value}），不能手写"]
    try:
        snap = store.get(snapshot_id)
    except SnapshotNotFound:
        return None, [f"找不到快照 {snapshot_id}：snapshot_id 必须照抄 workflow 工具返回的值"]
    if snap.kind != kind:
        return None, [f"快照 {snapshot_id} 的类型是 {snap.kind.value}，{what}需要 {kind.value}"]
    stale = _age_problems(snap.as_of, now, f"{what}引用的快照 {snapshot_id}")
    return (None, stale) if stale else (snap, [])


def _required_steps_in_order(plan: Plan, required: list[str]) -> list[str]:
    """必备步骤都在、相对顺序不变（可在中间插入其他步骤）。"""
    seq = [s.type.value for s in sorted(plan.steps, key=lambda s: s.order)]
    pos = 0
    for t in required:
        try:
            pos = seq.index(t, pos) + 1
        except ValueError:
            return [f"必备步骤缺失或顺序被改动：草案要求依次包含 {' → '.join(required)}，方案为 {' → '.join(seq)}"]
    return []


def check_plan_snapshot(plan: Plan, store: SnapshotStore) -> list[str]:
    snap, problems = _snapshot(store, plan.snapshot_id, SnapshotKind.PLAN_CONTEXT, "方案")
    if snap is None:
        return problems
    r = snap.result
    if plan.kind.value != r["plan"]["kind"]:
        problems.append(f"方案类型 {plan.kind.value} 与草案快照 {r['plan']['kind']} 不一致")
    if plan.panel is not None and plan.panel != snap.panel:
        problems.append(f"工作面 {plan.panel} 与草案快照 {snap.panel} 不一致")
    want = r["plan"]["context"]
    got = plan.context.model_dump(mode="json")
    diff = [f"{k}（快照 {want.get(k)!r}，提交 {got.get(k)!r}）" for k in sorted(set(want) | set(got)) if want.get(k) != got.get(k)]
    if diff:
        problems.append(
            "现场条件与草案快照不一致，不能手改（需要人工确认的条件请重新调用 draft_plan 传入 human_inputs）：" + "；".join(diff)
        )
    problems += _required_steps_in_order(plan, r["required_steps"])
    return problems


def validate_plan(tool: str, plan: Plan, now: datetime | None, store: SnapshotStore | None = None) -> list[str]:
    """返回全部问题；空列表表示可以发布。now 为 None 时不检查时效（自查场景）。"""
    problems = []
    expected = PLAN_TOOLS.get(tool)
    if expected and plan.kind != expected:
        problems.append(f"方案类型应为 {expected.value}，实际为 {plan.kind.value}")
    problems += _age_problems(plan.context.as_of, now, "现场条件")
    if store is not None:
        problems += check_plan_snapshot(plan, store)
    violations = check_plan(plan)
    if violations:
        problems.append(format_violations(violations))
    return problems


def plan_notes(plan: Plan, store: SnapshotStore) -> list[str]:
    """给确认人看的现场条件说明：哪些由人工输入（值和确认人照草案快照列出）、哪些系统没有数据也没有人确认。

    人工输入经智能体转述，系统无法核实确认人是否真的确认过，所以必须在人工确认弹窗里逐项列出，由确认人把关。
    """
    try:
        snap = store.get(plan.snapshot_id or "")
    except SnapshotNotFound:
        return []
    ctx = plan.context.model_dump(mode="json")

    def show(v: Any) -> str:
        return {True: "是", False: "否"}.get(v, str(v)) if isinstance(v, bool) else str(v)

    given = [
        f"{HUMAN_INPUT_LABELS.get(h['field'], h['field'])}={show(h['value'])}（确认人：{h['confirmed_by']}）"
        for h in snap.inputs.get("human_inputs") or []
    ]
    missing = [label for f, label in HUMAN_INPUT_LABELS.items() if ctx.get(f) is None]
    notes = []
    if given:
        notes.append("以下现场条件为人工输入，系统无法核实，请确认人逐项核对：" + "；".join(given))
    if missing:
        notes.append("以下现场条件系统没有数据、也未经人工确认（依赖它们的红线按不通过处理）：" + "、".join(missing))
    return notes


# ── 第二版：按记录核对 ──────────────────────────────────────────────────────

# 指令 → 它引用的记录必须是哪个技能、哪个脚本的结果
RECORD_SOURCES: dict[str, tuple[str, str]] = {
    "publish_warning": ("fire-assessment", "warning"),
    "escalate_emergency": ("emergency-escalation", "escalation"),
}


def _record(records: RecordStore | None, record_id: str, tool: str, what: str, now: datetime | None) -> tuple[Record | None, list[ApprovalCheck]]:
    """取记录并逐项核对，返回（记录, 核对项）。任一项不通过时记录为 None。"""
    checks: list[ApprovalCheck] = []

    def fail(name: str, detail: str) -> tuple[None, list[ApprovalCheck]]:
        checks.append(ApprovalCheck(name=name, ok=False, detail=detail))
        return None, checks

    if records is None:
        return fail("引用记录", "actions 没有配置记录目录，不能核对记录（fail closed）")
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


def _both(snapshot_id: str | None, record_id: str | None) -> list[str]:
    return ["snapshot_id 和 record_id 只能给一个：第二版引用记录（record_id），第一版引用快照（snapshot_id）"] if snapshot_id and record_id else []


def check_warning_record(
    notice: WarningNotice, records: RecordStore | None, now: datetime | None
) -> tuple[list[str], list[str], list[ApprovalCheck]]:
    """（问题, 给确认人看的说明, 核对项）。"""
    rec, checks = _record(records, notice.record_id or "", "publish_warning", "预警", now)
    if rec is None:
        return [c.detail or c.name for c in checks if not c.ok], [], checks
    r = _result(rec)
    if rec.panel and rec.panel not in notice.location:
        checks.append(ApprovalCheck(name="工作面", ok=False, detail=f"预警位置“{notice.location}”与记录的工作面 {rec.panel} 不符"))
        return [checks[-1].detail or ""], [], checks
    computed = r.get("level")
    if computed not in NAMES:
        checks.append(ApprovalCheck(name="预警等级", ok=False, detail=f"记录 {rec.record_id} 里没有可核对的预警等级"))
        return [checks[-1].detail or ""], [], checks
    if notice.level.value == computed:
        checks.append(ApprovalCheck(name="预警等级", ok=True, detail=f"与计算结果一致（{NAMES[computed]}）"))
        return [], [], checks
    if not (notice.override_reason or "").strip():
        msg = f"预警等级 {NAMES[notice.level.value]} 与记录 {rec.record_id} 的计算结果 {NAMES[computed]} 不一致；人工改等级须在 override_reason 写明理由"
        checks.append(ApprovalCheck(name="预警等级", ok=False, detail=msg))
        return [msg], [], checks
    note = f"与计算结果不一致：计算 {NAMES[computed]}，提交 {NAMES[notice.level.value]}，理由：{notice.override_reason}"
    checks.append(ApprovalCheck(name="预警等级", ok=True, detail=note))
    return [], [note], checks


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


def check_warning(notice: WarningNotice, store: SnapshotStore, now: datetime | None = None) -> tuple[list[str], list[str]]:
    """第一版（快照）：（问题, 给确认人看的说明）。等级与研判快照不同且没写理由 → 拒绝；写了理由 → 放行并在确认时提示。"""
    snap, problems = _snapshot(store, notice.snapshot_id, SnapshotKind.ASSESSMENT, "预警", now)
    if snap is None:
        return problems, []
    computed = snap.result["warning"]["level"]
    if notice.level.value == computed:
        return [], []
    if not (notice.override_reason or "").strip():
        return [
            f"预警等级 {NAMES[notice.level.value]} 与研判快照计算结果 {NAMES[computed]} 不一致；人工改等级须在 override_reason 写明理由"
        ], []
    return [], [f"与计算结果不一致：计算 {NAMES[computed]}，提交 {NAMES[notice.level.value]}，理由：{notice.override_reason}"]


def check_escalation(esc: Escalation, store: SnapshotStore, now: datetime | None = None) -> list[str]:
    snap, problems = _snapshot(store, esc.snapshot_id, SnapshotKind.ESCALATION, "升级", now)
    if snap is None:
        return problems
    if not snap.result.get("recommend"):
        return [f"升级建议快照 {esc.snapshot_id} 的结论是不建议升级；需要升级时由指挥人员另行决定，不经本工具提交"]
    return []


def check_dispatch(order: DispatchOrder, store: SnapshotStore) -> list[str]:
    """引用了调度草案快照时逐行核对物资、数量、起止节点；没有引用时放行（调度单仍需人工确认）。"""
    if not order.snapshot_id:
        return []
    snap, problems = _snapshot(store, order.snapshot_id, SnapshotKind.DISPATCH, "调度单")
    if snap is None:
        return problems
    want = sorted((ln["item_type"], float(ln["quantity"]), ln["from_node"], ln["to_node"]) for ln in snap.result["order"]["lines"])
    got = sorted((ln.item_type, float(ln.quantity), ln.from_node, ln.to_node) for ln in order.lines)
    return [] if want == got else [f"调度单明细与草案快照 {order.snapshot_id} 不一致：快照 {want}，提交 {got}"]


ACTION_NAMES = {
    "publish_warning": "发布预警等级",
    "issue_firefighting_plan": "下发防灭火方案",
    "escalate_emergency": "升级为应急响应",
    "notify_evacuation": "发布补充撤人名单",
    "issue_dispatch_order": "下发物资调度单",
    "issue_sealing_plan": "下发封闭方案",
    "approve_unsealing": "批准启封",
}


def _summary(tool: str, arguments: dict[str, Any], problems: list[str], notes: list[str], checks: list[ApprovalCheck]) -> dict[str, Any]:
    """审批弹窗的结构化内容（ApprovalSummary）：要做什么、引用哪些记录、核对结果、需要确认人把关的事项。"""
    body = next((v for v in arguments.values() if isinstance(v, dict)), arguments) if arguments else {}
    action = ACTION_NAMES.get(tool, tool)
    if tool == "publish_warning" and body.get("location"):
        action = f"发布 {body['location']} {NAMES.get(str(body.get('level')), body.get('level'))}预警"
    if not checks:  # 第一版或没有逐项核对的指令：把预检结论作为一项
        checks = [ApprovalCheck(name="红线与快照预检", ok=not problems, detail="；".join(problems) or None)]
    ids = [x for x in (body.get("record_id"), body.get("snapshot_id")) if isinstance(x, str) and x]
    return ApprovalSummary(
        action=action, tool=tool, record_ids=ids, checks=checks, human_items=notes,
        override_reason=body.get("override_reason") if isinstance(body.get("override_reason"), str) else None,
    ).model_dump(mode="json")  # fmt: skip


def precheck(
    tool: str, arguments: dict[str, Any], now: datetime | None, store: SnapshotStore | None = None, records: RecordStore | None = None
) -> dict[str, Any]:
    """arguments 为工具调用参数。返回 {ok, problems, notes, summary}；notes 给确认人看（如人工改等级的理由），
    summary 是审批弹窗的结构化内容（ApprovalSummary）。records 为第二版的记录库（引用 record_id 时核对）。"""
    notes: list[str] = []
    checks: list[ApprovalCheck] = []
    try:
        body = arguments.get({"publish_warning": "notice", "escalate_emergency": "escalation"}.get(tool, ""), arguments)
        has_record = isinstance(body, dict) and bool(body.get("record_id"))
        if tool == "publish_warning" and has_record:
            n = WarningNotice.model_validate(body)
            problems = _both(n.snapshot_id, n.record_id) or []
            if not problems:
                problems, notes, checks = check_warning_record(n, records, now)
        elif tool == "escalate_emergency" and has_record:
            e = Escalation.model_validate(body)
            problems = _both(e.snapshot_id, e.record_id) or []
            if not problems:
                problems, checks = check_escalation_record(e, records, now)
        elif tool in PLAN_TOOLS:
            plan = Plan.model_validate(arguments.get("plan", arguments))
            problems = validate_plan(tool, plan, now, store)
            if store is not None and not problems:
                notes = plan_notes(plan, store)
        elif store is None:
            problems = []
        elif tool == "publish_warning":
            problems, notes = check_warning(WarningNotice.model_validate(arguments.get("notice", arguments)), store, now)
        elif tool == "escalate_emergency":
            problems = check_escalation(Escalation.model_validate(arguments.get("escalation", arguments)), store, now)
        elif tool == "issue_dispatch_order":
            problems = check_dispatch(DispatchOrder.model_validate(arguments.get("order", arguments)), store)
        else:
            problems = []
    except Exception as exc:
        problems = [f"指令格式不符合数据契约：{exc}"]
        return {"ok": False, "problems": problems, "notes": [], "summary": _summary(tool, {}, problems, [], [])}
    return {"ok": not problems, "problems": problems, "notes": notes, "summary": _summary(tool, arguments, problems, notes, checks)}


__all__ = [
    "CONTEXT_MAX_AGE",
    "PLAN_TOOLS",
    "RECORD_SOURCES",
    "WarningLevel",
    "check_escalation_record",
    "check_plan_snapshot",
    "check_warning_record",
    "plan_notes",
    "precheck",
    "validate_plan",
]
