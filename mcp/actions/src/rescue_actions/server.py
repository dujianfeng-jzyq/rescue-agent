"""actions 服务：所有“需要人工确认”的指令都在这里发布。

边界约定：只读和计算类工具分布在其他服务；凡是会对外产生作用的指令（发布预警、下发方案、调度、通知撤人、
启封……）只能通过本服务，本服务的每个 MCP 工具都需要人工确认。这样 DSH 的审批插件只需拦住
mcp__actions__ 前缀的工具；只有总指挥协调 Agent 挂载本服务，专业 Agent 无法发布指令。
方案自查用 kb 服务的 check_plan_redlines（只读，与本服务共用同一套校验逻辑）。

三道防线：
1. DSH rescue-approval 插件：调用前先请求 /redlines/check 预检，违规直接拒绝；通过后弹出人工确认。
2. 指挥员人工确认。
3. 本服务在执行前再用同一个红线引擎强制校验 —— 即使 DSH 配置被改错（例如审批被关掉），违规指令也发不出去。

TODO(工程): 指令目前只写入台账（JSONL）；对接调度台、广播、短信等下发通道，以及记录确认人身份。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel
from rescue_contract.client import MineData, MineDataError
from rescue_contract.models import (
    DispatchOrder,
    Escalation,
    EvacuationNotice,
    MineTime,
    Plan,
    StepType,
    WarningNotice,
)
from rescue_contract.serving import sim_control_headers
from rescue_contract.snapshots import SnapshotStore
from rescue_contract.validation import PLAN_TOOLS, check_dispatch, check_escalation, check_warning, plan_notes, precheck, validate_plan
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("rescue.actions")

INSTRUCTIONS = (
    "指令发布服务。每个工具都会触发人工确认，确认后才执行。"
    "方案必须结构化（步骤类型、顺序、人数、现场条件），提交前应已由合规审核 Agent 自查红线。"
    "被拒绝时照实告诉指挥人员拒绝原因和条号，不要改写规避。"
)
SIM_STEP_TYPES = {
    StepType.INERT_INJECTION,
    StepType.GROUTING,
    StepType.INHIBITOR_SPRAY,
    StepType.LEAK_SEALING,
    StepType.PRESSURE_BALANCING,
    StepType.TEMPORARY_SEAL,
    StepType.PERMANENT_SEAL,
    StepType.UNSEAL,
    StepType.EVACUATE,
}


class ActionReceipt(BaseModel):
    action_id: str
    tool: str
    issued_at: MineTime
    status: str
    forwarded_to_sim: bool = False
    note: str | None = None


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


class Sim:
    """模拟矿井的处置执行通道（仅模拟环境；真实矿井由人执行，不存在此通道）。"""

    def __init__(self, base: str | None) -> None:
        self.base = base.rstrip("/") if base else None

    def apply(self, iv_type: str, params: dict | None = None, roadways: list[str] | None = None) -> bool:
        if not self.base:
            return False
        body = json.dumps({"type": iv_type, "params": params or {}, "roadways": roadways or [], "source": "action"}).encode()
        req = urllib.request.Request(
            f"{self.base}/interventions", data=body, headers={"Content-Type": "application/json", **sim_control_headers()}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5):
                return True
        except OSError as exc:
            log.warning("转发模拟处置失败：%s", exc)
            return False


def build_server(
    mine_data_target: Any | None = None,
    ledger_path: Path | None = None,
    sim_control_url: str | None = None,
    store: SnapshotStore | None = None,
) -> MCPServer:
    md = MineData(mine_data_target) if mine_data_target is not None else MineData.from_env()
    ledger = Ledger(ledger_path or Path(os.environ.get("RESCUE_ACTIONS_LEDGER", "var/actions-ledger.jsonl")))
    sim = Sim(sim_control_url if sim_control_url is not None else os.environ.get("RESCUE_SIM_CONTROL_URL"))
    snapshots = store or SnapshotStore()
    server = MCPServer("actions", instructions=INSTRUCTIONS)

    async def mine_now() -> datetime | None:
        try:
            return await md.now()
        except (MineDataError, OSError, Exception):  # noqa: BLE001
            return None

    TIMED_TOOLS = {*PLAN_TOOLS, "publish_warning", "escalate_emergency"}
    NO_CLOCK = "无法取得矿井时间，不能核对研判快照和现场条件的时效，指令未发布。请检查 mine_data 服务后重试。"

    async def require_now(tool: str, payload: BaseModel) -> datetime:
        """需要核对时效的指令：取不到矿井时间就拒绝（fail closed），不能跳过时效检查。"""
        now = await mine_now()
        if now is None:
            ledger.append(
                {"tool": tool, "status": "rejected", "at": None, "problems": [NO_CLOCK], "payload": payload.model_dump(mode="json")}
            )
            raise ToolError(NO_CLOCK)
        return now

    async def issue(tool: str, payload: BaseModel, forward: bool = False, note: str | None = None) -> ActionReceipt:
        now = await mine_now() or datetime.now()
        receipt = ActionReceipt(
            action_id=f"A-{uuid.uuid4().hex[:10]}", tool=tool, issued_at=now, status="issued", forwarded_to_sim=forward, note=note
        )
        ledger.append({**receipt.model_dump(), "payload": payload.model_dump(mode="json")})
        log.info("action %s %s", tool, receipt.action_id)
        return receipt

    async def reject_if(tool: str, payload: BaseModel, problems: list[str]) -> None:
        if problems:
            ledger.append(
                {
                    "tool": tool,
                    "status": "rejected",
                    "at": await mine_now(),
                    "problems": problems,
                    "payload": payload.model_dump(mode="json"),
                }
            )
            raise ToolError("指令未发布。\n" + "\n".join(problems))

    async def issue_plan(tool: str, plan: Plan) -> ActionReceipt:
        now = await require_now(tool, plan)
        problems = validate_plan(tool, plan, now, snapshots)
        if problems:
            ledger.append({"tool": tool, "status": "rejected", "at": now, "problems": problems, "payload": plan.model_dump(mode="json")})
            raise ToolError("指令未发布。\n" + "\n".join(problems))
        forwarded = False
        for step in sorted(plan.steps, key=lambda s: s.order):
            if step.type in SIM_STEP_TYPES:
                forwarded = sim.apply(step.type.value, dict(step.params), step.roadways) or forwarded
        return await issue(tool, plan, forward=forwarded, note="；".join(plan_notes(plan, snapshots)) or None)

    # ── 需要确认（DSH 审批插件拦截）──

    @server.tool()
    async def publish_warning(notice: WarningNotice) -> ActionReceipt:
        """发布自燃预警等级（确认人：通风部门负责人）。snapshot_id 照抄 workflow 的 assess_panel 返回值；
        等级必须与研判快照一致，人工改等级时在 override_reason 写明理由。"""
        problems, notes = check_warning(notice, snapshots, await require_now("publish_warning", notice))
        await reject_if("publish_warning", notice, problems)
        return await issue("publish_warning", notice, note="；".join(notes) or None)

    @server.tool()
    async def issue_firefighting_plan(plan: Plan) -> ActionReceipt:
        """下发防灭火方案（确认人：总工程师）。方案必须基于 workflow 的 draft_plan（snapshot_id 照抄），
        现场条件和必备步骤按快照核对；执行前强制红线校验。"""
        return await issue_plan("issue_firefighting_plan", plan)

    @server.tool()
    async def escalate_emergency(escalation: Escalation) -> ActionReceipt:
        """建议升级为应急响应（确认人：总工程师或矿长）。snapshot_id 照抄 workflow 的 recommend_escalation 返回值。"""
        await reject_if(
            "escalate_emergency", escalation, check_escalation(escalation, snapshots, await require_now("escalate_emergency", escalation))
        )
        return await issue("escalate_emergency", escalation)

    @server.tool()
    async def notify_evacuation(notice: EvacuationNotice) -> ActionReceipt:
        """发布补充撤人名单与避灾路线（确认人：调度员）。这是对预案撤人的补充，不替代、不延迟预案撤人。"""
        forwarded = False
        try:
            where = {p.person_id: p.roadway_id for p in await md.personnel()}
            roadways = sorted({where[p] for p in notice.persons if p in where})
            forwarded = bool(roadways) and sim.apply("evacuate", roadways=roadways)
        except Exception:  # noqa: BLE001
            pass
        return await issue("notify_evacuation", notice, forward=forwarded)

    @server.tool()
    async def issue_dispatch_order(order: DispatchOrder) -> ActionReceipt:
        """下发物资调度单（确认人：指挥部指定负责人）。草案由 workflow 的 draft_dispatch_for_plan 生成时，
        snapshot_id 照抄，明细按快照核对。"""
        await reject_if("issue_dispatch_order", order, check_dispatch(order, snapshots))
        return await issue("issue_dispatch_order", order)

    @server.tool()
    async def issue_sealing_plan(plan: Plan) -> ActionReceipt:
        """下发封闭或灭火方案（确认人：总指挥 + 救护队带队指挥员）。方案必须基于 workflow 的 draft_plan
        （snapshot_id 照抄），现场条件和必备步骤按快照核对；执行前强制红线校验。"""
        return await issue_plan("issue_sealing_plan", plan)

    @server.tool()
    async def approve_unsealing(plan: Plan) -> ActionReceipt:
        """批准启封方案（确认人：总指挥）。方案必须基于 workflow 的 draft_plan（snapshot_id 照抄），
        现场条件和必备步骤按快照核对；执行前强制红线校验。"""
        return await issue_plan("approve_unsealing", plan)

    # ── DSH 审批插件的预检接口（HTTP，不是 MCP 工具）──

    @server.custom_route("/redlines/check", methods=["POST"])
    async def redlines_check(req: Request) -> JSONResponse:
        body = await req.json()
        now = await mine_now()
        if now is None and body.get("tool", "") in TIMED_TOOLS:  # 撤人名单、调度单不核对时效，时钟故障时不能拦
            return JSONResponse({"ok": False, "problems": [NO_CLOCK], "notes": []})
        result = precheck(body.get("tool", ""), body.get("arguments") or {}, now, snapshots)
        return JSONResponse(result)

    return server
