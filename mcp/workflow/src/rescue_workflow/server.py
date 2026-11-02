"""workflow 服务：确定性流程。每个工具按固定步骤取数、计算、写研判快照，并返回由模板生成的报告事实部分。

只读：不发布任何指令，只写快照（var/snapshots/）和值守事件。见 docs/workflow-design.md。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from rescue_contract.client import MineData
from rescue_contract.models import HumanInput, MineTime, Plan, Snapshot, SnapshotKind
from rescue_contract.serving import DEFAULT_PORTS
from rescue_contract.snapshots import SnapshotStore, new_id
from rescue_contract.validation import check_plan_snapshot, precheck
from rescue_fire_model import explosion

from . import assess, dispatch_plan, escalation, monitor, plan, reports, timeline

INSTRUCTIONS = (
    "工作流服务：研判、效果评估、升级建议、方案草案等标准流程按固定步骤由代码完成，结果写成带编号的研判快照。"
    "回答时把 report_markdown 原样单独成段放进回答，不改写、不删减；分析与建议另起一段，只引用报告里已有的数值。"
    "提交指令时 snapshot_id 照抄这里返回的值。"
)


def build_server(mine_data_target: Any | None = None, store: SnapshotStore | None = None) -> MCPServer:
    md = MineData(mine_data_target) if mine_data_target is not None else MineData.from_env()
    snapshots = store or SnapshotStore()
    xc = explosion.load_constants()
    public_url = (
        os.environ.get("RESCUE_FIRE_MODEL_PUBLIC_URL")
        or f"http://127.0.0.1:{os.environ.get('RESCUE_FIRE_MODEL_PORT', DEFAULT_PORTS['fire_model'])}"
    )
    server = MCPServer("workflow", instructions=INSTRUCTIONS)

    def save(
        kind: SnapshotKind, panel: str, as_of: str, inputs: dict, result: dict, report, parents: list[str] | None = None, bind=None
    ) -> Snapshot:
        at = datetime.fromisoformat(as_of)
        sid = new_id(at)
        if bind is not None:  # 结果里需要带上自己的快照编号（如方案的 snapshot_id）
            bind(result, sid)
        snap = Snapshot(
            snapshot_id=sid, kind=kind, panel=panel, as_of=at, created_at=datetime.now().replace(microsecond=0),
            inputs=inputs, result=result, report_markdown=report(result, sid), parent_ids=parents or [],
        )  # fmt: skip
        return snapshots.put(snap)

    @server.tool()
    async def assess_panel(panel: str = "1201", include_personnel: bool = False) -> dict[str, Any]:
        """研判快照（标准研判一律先调它）：按固定步骤完成实时报警、预警等级和自燃发展阶段、触发指标的趋势、
        全部束管测点的爆炸三角形（取最不利）、受威胁人员（等级达到橙色或 include_personnel=true 时），
        写成研判快照并返回 snapshot_id、report_markdown（报告事实部分，含风险图）和结构化结果 result。
        回答时把 report_markdown 原样单独成段放进回答；不要另外再调 assess_warning_level、gas_trend、
        assess_explosion_risk 重复研判。阈值和判据为开发占位值。"""
        result = await assess.assess_panel(md, panel, xc, public_url, include_personnel)
        snap = save(
            SnapshotKind.ASSESSMENT,
            panel,
            result["as_of"],
            {"panel": panel, "include_personnel": include_personnel},
            result,
            reports.assessment,
        )
        return {"snapshot_id": snap.snapshot_id, "report_markdown": snap.report_markdown, "result": result}

    async def new_assessment(panel: str, include_personnel: bool) -> Snapshot:
        result = await assess.assess_panel(md, panel, xc, public_url, include_personnel)
        return save(
            SnapshotKind.ASSESSMENT,
            panel,
            result["as_of"],
            {"panel": panel, "include_personnel": include_personnel},
            result,
            reports.assessment,
        )

    @server.tool()
    async def review_effect(panel: str = "1201", since: MineTime | None = None) -> dict[str, Any]:
        """处置效果评估（评估效果一律先调它）：对工作面全部发火指标测点比较处置前后的上升速率，给出总体结论
        （已受控 / 需继续处置 / 恶化 / 观测时间不足）、各结论测点数、逐点结果和下次复评时刻，写成效果评估快照。
        since 为处置开始的矿井时间；不给时取指令台账里本工作面最近一次已下发防灭火方案的下发时间。
        回答时把 report_markdown 原样单独成段放进回答；结论为恶化时再调 recommend_escalation。"""
        result = await escalation.review_effect(md, panel, since)
        latest = snapshots.latest(SnapshotKind.ASSESSMENT, panel)
        snap = save(
            SnapshotKind.EFFECT, panel, result["as_of"], {"panel": panel, "since": result["since"]}, result,
            reports.effect_review, [latest.snapshot_id] if latest else [],
        )  # fmt: skip
        return {"snapshot_id": snap.snapshot_id, "report_markdown": snap.report_markdown, "result": result}

    @server.tool()
    async def recommend_escalation(panel: str = "1201") -> dict[str, Any]:
        """是否升级为应急响应的建议（回答“要不要升级”一律先调它）：重新生成研判快照，结合最近一次效果评估，
        按预设判据（thresholds.yaml 的 escalation 节，占位值）给出 recommend、命中的判据和规程条文原文，写成升级建议快照。
        回答时把 report_markdown 原样单独成段放进回答；条文已在报告里，不必再检索。
        提交 actions 的 escalate_emergency 时 snapshot_id 照抄这里返回的值（只有建议升级的快照才能提交）。"""
        a = await new_assessment(panel, include_personnel=True)
        eff = snapshots.latest(SnapshotKind.EFFECT, panel)
        result = escalation.recommend(a.result, eff.result if eff else None)
        result["effect_snapshot_id"] = eff.snapshot_id if eff else None
        parents = [a.snapshot_id] + ([eff.snapshot_id] if eff else [])
        snap = save(SnapshotKind.ESCALATION, panel, result["as_of"], {"panel": panel}, result, reports.escalation, parents)
        return {
            "snapshot_id": snap.snapshot_id,
            "assessment_snapshot_id": a.snapshot_id,
            "report_markdown": a.report_markdown + "\n\n" + snap.report_markdown,
            "result": result,
        }

    @server.tool()
    async def draft_dispatch_for_plan(plan: Plan, destination_node: str | None = None) -> dict[str, Any]:
        """按方案生成调度单草案（方案需要物资时调它）：plan 为 rescue-plan 返回的完整方案（必须与草案快照一致）。
        代码按密闭步骤填写的巷道数算密闭材料、按注浆步骤算注浆材料、比较库存和制氮产能，库存够的物资生成调度单草案，
        写成调度草案快照。下发时 issue_dispatch_order 的 order 照抄返回的 order（含 snapshot_id），明细按快照核对。
        destination_node 不给时取本工作面进风巷靠工作面一端。缺口物资需人工联系外部来源。"""
        problems = check_plan_snapshot(plan, snapshots)
        if problems:
            raise ToolError("方案与草案快照不一致，不能据此调度：\n" + "\n".join(problems))
        panel = plan.panel or snapshots.get(plan.snapshot_id).panel
        result = await dispatch_plan.draft(md, plan, panel, destination_node)

        def bind(r: dict, sid: str) -> None:
            if r["order"] is not None:
                r["order"]["snapshot_id"] = sid

        as_of = snapshots.get(plan.snapshot_id).as_of.isoformat()
        snap = save(SnapshotKind.DISPATCH, panel, as_of, {"plan": plan.model_dump(mode="json"), "destination_node": destination_node}, result,
                    reports.dispatch_draft, [plan.snapshot_id], bind)  # fmt: skip
        return {
            "snapshot_id": snap.snapshot_id,
            "order": snap.result["order"],
            "report_markdown": snap.report_markdown,
            "result": snap.result,
        }

    @server.tool()
    async def get_monitor_events(panel: str = "1201", since: MineTime | None = None) -> dict[str, Any]:
        """值守事件（值守进程不经过大模型定时检查：新报警、预警等级或自燃阶段变化、启封后按班复评）。
        since 不给时取最近 24 小时（矿井时间）。每条事件带研判快照编号，详情用 assess_panel 重新研判。"""
        now = await md.now()
        start = since or now - timedelta(hours=24)
        events = [e for e in monitor.read_events(start, panel) if datetime.fromisoformat(e["mine_time"]) <= now]
        return {"panel": panel, "count": len(events), "events": events}

    @server.tool()
    async def review_timeline(panel: str = "1201", start: MineTime | None = None, end: MineTime | None = None) -> dict[str, Any]:
        """复盘时间线：指令台账（发布、被服务端拒绝）、值守事件、研判快照按矿井时间排序，审计日志按系统时间单列。
        用作复盘报告的事实部分，report_markdown 原样放进回答。"""
        result = timeline.build(panel, snapshots, start, end)
        return {"report_markdown": reports.timeline(result), "result": result}

    PLAN_TOOL = {"sealing": "issue_sealing_plan", "unsealing": "approve_unsealing", "firefighting": "issue_firefighting_plan"}

    @server.tool()
    async def draft_plan(
        kind: str,
        panel: str = "1201",
        human_inputs: list[HumanInput] | None = None,
        leak_m3_min: float | None = None,
        target_o2_pct: float | None = None,
    ) -> dict[str, Any]:
        """方案草案（编写防灭火、封闭、启封方案一律先调它）。kind：firefighting / sealing / unsealing。
        先重新生成研判快照，再由代码生成现场条件 context（甲烷、是否上升、氧浓度、爆炸危险性、封闭时间）和必备步骤，
        写成方案草案快照，并用发布时同一套核对逻辑做红线自查。
        系统没有数据的现场条件（灾区温度、能见度、火源是否明确、水源是否充足、回风道是否畅通）只能由人确认后
        通过 human_inputs 传入（写明 confirmed_by），不要自己填；leak_m3_min、target_o2_pct 给出时由代码估算注氮流量。
        返回 plan（完整方案，含 snapshot_id）、editable（可以改的部分）、fixed（不能改的部分）、missing_inputs、
        blocking_inputs（红线自查因缺少它们而不通过时才非空：只有这些必须先问人；为空时缺失项列为待补充，可以继续提交）、
        redline_check 和 report_markdown。在 plan 上只改 editable 列出的部分；context、snapshot_id 和必备步骤改了会在发布时被拒绝。"""
        a = await new_assessment(panel, include_personnel=(kind == "sealing"))
        inputs = list(human_inputs or [])
        result = await plan.draft(md, kind, panel, a.result, inputs, leak_m3_min, target_o2_pct)

        def bind(r: dict, sid: str) -> None:
            r["plan"]["snapshot_id"] = sid
            r["redline_check"] = None

        args = {
            "kind": kind,
            "panel": panel,
            "human_inputs": [h.model_dump(mode="json") for h in inputs],
            "leak_m3_min": leak_m3_min,
            "target_o2_pct": target_o2_pct,
        }
        snap = save(SnapshotKind.PLAN_CONTEXT, panel, result["as_of"], args, result, lambda r, sid: "", [a.snapshot_id], bind)
        check = precheck(PLAN_TOOL[kind], {"plan": snap.result["plan"]}, None, snapshots)
        # 人工确认项只有在红线自查因此不通过时才阻塞提交；否则列为待补充，由确认人在人工确认时把关
        human_missing = [f for f in plan.HUMAN_FIELDS if snap.result["plan"]["context"].get(f) is None]
        blocking = [] if check["ok"] else human_missing
        out = {**snap.result, "redline_check": check, "blocking_inputs": blocking}
        return {
            "snapshot_id": snap.snapshot_id,
            "assessment_snapshot_id": a.snapshot_id,
            "plan": out["plan"],
            "editable": out["editable"],
            "fixed": out["fixed"],
            "missing_inputs": out["missing_inputs"],
            "blocking_inputs": blocking,
            "redline_check": check,
            "report_markdown": reports.plan_draft(out, snap.snapshot_id),
            "result": out,
        }

    return server
