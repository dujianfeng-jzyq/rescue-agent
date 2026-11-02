"""指令发布前的校验：红线 + 方案类型 + 数据时效 + 研判快照核对。

actions 服务（强制执行）、DSH 审批插件的预检接口、kb 服务的自查工具共用这里的逻辑。
快照核对（docs/workflow-design.md W6）：方案的现场条件和必备步骤、预警等级、升级建议、调度单，
都要与 workflow 服务生成的研判快照一致——红线引擎的输入来自计算，不来自模型填写。
store 为 None 时不做快照核对（只用于单元测试红线和时效逻辑）。
now 为 None 时不检查时效（只用于自查）；发布和审批预检必须传入矿井时间，取不到时由调用方拒绝（fail closed）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .models import HUMAN_INPUT_LABELS, DispatchOrder, Escalation, Plan, PlanKind, Snapshot, SnapshotKind, WarningLevel, WarningNotice
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


def check_warning(notice: WarningNotice, store: SnapshotStore, now: datetime | None = None) -> tuple[list[str], list[str]]:
    """（问题, 给确认人看的说明）。等级与研判快照不同且没写理由 → 拒绝；写了理由 → 放行并在确认时提示。"""
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


def precheck(tool: str, arguments: dict[str, Any], now: datetime | None, store: SnapshotStore | None = None) -> dict[str, Any]:
    """arguments 为工具调用参数。返回 {ok, problems, notes}；notes 给确认人看（如人工改等级的理由）。"""
    notes: list[str] = []
    try:
        if tool in PLAN_TOOLS:
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
        return {"ok": False, "problems": [f"指令格式不符合数据契约：{exc}"], "notes": []}
    return {"ok": not problems, "problems": problems, "notes": notes}


__all__ = ["CONTEXT_MAX_AGE", "PLAN_TOOLS", "WarningLevel", "check_plan_snapshot", "plan_notes", "precheck", "validate_plan"]
