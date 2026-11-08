"""guard 守卫服务（第三版 P2；由第二版的 actions 演变而来，服务名、端口、工具名不变）：智能体所有对外动作的唯一通道。

边界约定：
- rescue-mine（矿井接口）只执行、不判断；红线、记录核对、批准、台账都在这一侧。换矿不用重审。
- 对外动作工具（publish_warning、escalate_emergency、notify_evacuation、issue_dispatch_order、issue_sealing_plan、issue_firefighting_plan、approve_unsealing）都是 C 级动作
  （rescue_contract.action_policy）：先预检，再人工确认，本服务在执行前强制校验，通过后才调用 rescue-mine 的
  send_notice / dispatch_order / submit_treatment（写入令牌只有本服务持有）。
- 只读和计算类工具分布在其他服务；方案自查用本服务的只读工具 precheck_plan，台账查询用 query_last_issued（A 级，不弹确认）。

三道防线：
1. DSH rescue-approval 插件：调用前先请求 /redlines/check 预检，违规直接拒绝；通过后弹出人工确认。
2. 指挥员人工确认。
3. 本服务在执行前再用同一个红线引擎强制校验 —— 即使 DSH 配置被改错（例如审批被关掉），违规指令也发不出去。

指令引用证据插件写的结果记录（record_id），本服务只读记录目录（RESCUE_RECORD_DIR），发布前重算 content_sha256 并按记录核对。
封闭方案只提交 draft_sealing_plan 的审核记录编号，方案从记录里取（不经模型转抄，也不在模型上下文里来回携带）。

指令台账（JSONL）每条带：来源记录编号（record_ids）、Origin（谁触发的，取自记录）、rescue-mine 的回执（mine_receipts）和事件编号
（incident_id，第三版 P4 起填写：该工作面未关闭的火情事件）。真实矿井异步生效时，回执用 rescue-mine 的 get_action_status 查询。
相同指令（内容相同）重复提交时，幂等键相同，矿井只执行一次。

TODO(工程): 记录确认人身份（批准人按角色限定，P3/P4）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field
from rescue_contract.client import MineData
from rescue_contract.incidents import IncidentStore
from rescue_contract.ledger import for_panel
from rescue_contract.ledger import records as ledger_records
from rescue_contract.models import (
    ActionStatus,
    DispatchOrder,
    DispatchRequest,
    Escalation,
    EvacuationNotice,
    MineReceipt,
    MineTime,
    NoticeKind,
    NoticeRequest,
    Plan,
    StepType,
    TreatmentRequest,
    WarningNotice,
)
from rescue_contract.records import RecordNotFound, RecordStore
from rescue_contract.redlines import RULES
from rescue_contract.validation import PLAN_TOOLS, plan_from_record, precheck, validate_plan
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("rescue.actions")

INSTRUCTIONS = (
    "守卫服务：所有对外动作（预警、撤人、调度、封闭方案、升级）都经这里。每个指令工具都会触发人工确认，确认后才执行。"
    "预警、升级、封闭方案都只提交固定流程返回的记录编号（record_id），等级、结论、方案由本服务按记录核对，不能手写。"
    "被拒绝时照实告诉指挥人员拒绝原因和条号，不要改写规避。"
)


class ActionReceipt(BaseModel):
    action_id: str
    tool: str
    issued_at: MineTime
    status: str  # issued：校验通过并已交给矿井执行；failed 不会作为回执返回（抛错）
    note: str | None = None
    # rescue-mine 的回执（每个动作一条；方案按步骤逐条）。矿井拒绝执行的动作（如需人工执行的监测步骤）在这里可见
    mine_receipts: list[MineReceipt] = Field(default_factory=list)
    mine_not_executed: list[str] = Field(default_factory=list)  # 矿井没有执行（rejected）的动作说明，需要人工执行或另行处理


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def idempotency_key(tool: str, payload: BaseModel | dict[str, Any], suffix: str = "") -> str:
    """同一个指令（工具 + 内容相同）得到同一个幂等键：智能体重试或重复提交，矿井只执行一次。"""
    body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    digest = hashlib.sha256(json.dumps([tool, body], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    return f"G-{digest}{suffix}"


def build_server(mine_data_target: Any | None = None, ledger_path: Path | None = None, records: RecordStore | None = None) -> MCPServer:
    md = MineData(mine_data_target) if mine_data_target is not None else MineData.from_env()
    ledger = Ledger(ledger_path or Path(os.environ.get("RESCUE_ACTIONS_LEDGER", "var/actions-ledger.jsonl")))
    record_store = records or RecordStore()  # 只读：本服务不写记录
    server = MCPServer("actions", instructions=INSTRUCTIONS)

    async def mine_now() -> datetime | None:
        try:
            return await md.now()
        except Exception:  # noqa: BLE001  取不到时钟（矿井接口不通）：调用方按 fail closed 处理
            return None

    TIMED_TOOLS = {*PLAN_TOOLS, "publish_warning", "escalate_emergency"}
    NO_CLOCK = "无法取得矿井时间，不能核对记录的时效，指令未发布。请检查矿井接口（rescue-mine）后重试。"

    def origin_of(record_ids: list[str]) -> dict[str, Any] | None:
        """台账里的 Origin：取自第一条能读出来的引用记录（记录已在预检里核对过完整性）。"""
        for rid in record_ids:
            try:
                return record_store.get(rid).origin.model_dump(mode="json")
            except Exception:  # noqa: BLE001
                continue
        return None

    incidents = IncidentStore()  # 对外动作的结果记进该工作面未关闭事件的时间线（第三版 P4）；读不到、写不了都不影响指令

    def incident_of(record_ids: list[str]) -> str | None:
        for rid in record_ids:
            try:
                found = incidents.find_open("fire", record_store.get(rid).panel)
            except Exception:  # noqa: BLE001
                continue
            if found:
                return found["incident_id"]
        return None

    def journal(entry: dict[str, Any]) -> None:
        """写台账，并把结果（通过、拒绝、失败）记进事件时间线。台账是依据，时间线是给人看的。"""
        ledger.append(entry)
        iid = entry.get("incident_id")
        if not iid:
            return
        at = str(entry.get("issued_at") or entry.get("at") or "")[:19]
        try:
            if not at:
                latest = incidents.entries(iid)[-1]["at"]
                at = latest
            incidents.append(
                iid, at, "note", "guard",
                {"type": "action", "tool": entry["tool"], "status": entry["status"], "action_id": entry.get("action_id"), "record_ids": entry["record_ids"], "problems": entry.get("problems", []),
                 "mine_receipt_ids": [r["action_id"] for r in entry.get("mine_receipts") or [] if r.get("action_id")]},
            )  # fmt: skip
        except Exception as exc:  # noqa: BLE001
            log.warning("事件时间线写入失败（指令不受影响）：%s", exc)

    def log_entry(tool: str, status: str, payload: BaseModel, record_ids: list[str], **extra: Any) -> dict[str, Any]:
        return {
            "tool": tool,
            "status": status,
            "record_ids": record_ids,
            "origin": origin_of(record_ids),
            "incident_id": incident_of(record_ids),  # 该工作面未关闭的火情事件（没有则为空）
            **extra,
            "payload": payload.model_dump(mode="json"),
        }

    async def require_now(tool: str, payload: BaseModel, record_ids: list[str]) -> datetime:
        """需要核对时效的指令：取不到矿井时间就拒绝（fail closed），不能跳过时效检查。"""
        now = await mine_now()
        if now is None:
            journal(log_entry(tool, "rejected", payload, record_ids, at=None, problems=[NO_CLOCK]))
            raise ToolError(NO_CLOCK)
        return now

    async def reject_if(tool: str, payload: BaseModel, problems: list[str], record_ids: list[str]) -> None:
        if problems:
            journal(log_entry(tool, "rejected", payload, record_ids, at=await mine_now(), problems=problems))
            raise ToolError("指令未发布。\n" + "\n".join(problems))

    async def deliver(tool: str, payload: BaseModel, record_ids: list[str], calls: list[Any], note: str | None = None, ordered: bool = False) -> ActionReceipt:
        """校验通过后交给矿井执行：calls 是按顺序执行的无参协程工厂，每个返回 MineReceipt。
        任一步失败（矿井接口不通、参数被矿井拒绝）就停止，台账记 failed 和已执行的回执，并如实告诉智能体已执行到哪一步。
        ordered=True（步骤有先后依赖，如启封必须先断电、撤人）时，矿井返回 rejected/failed 回执的步骤也算失败：不再执行后面的步骤。"""
        action_id = f"A-{uuid.uuid4().hex[:10]}"
        done: list[MineReceipt] = []
        try:
            for call in calls:
                r = await call()
                done.append(r)
                if ordered and r.status in (ActionStatus.REJECTED, ActionStatus.FAILED):
                    raise RuntimeError(f"步骤“{r.kind.value}”矿井未执行（{r.detail or r.status.value}），后续步骤依赖它，已停止")
        except Exception as exc:  # noqa: BLE001  矿井接口的任何失败都按未完成处理
            at = await mine_now()
            journal(
                log_entry(
                    tool, "failed", payload, record_ids, action_id=action_id, at=at,
                    mine_receipts=[r.model_dump(mode="json") for r in done], problems=[f"矿井执行失败：{exc}"],
                )  # fmt: skip
            )
            log.warning("action %s %s 矿井执行失败（已执行 %d/%d）：%s", tool, action_id, len(done), len(calls), exc)
            raise ToolError(
                f"指令已通过校验，但矿井接口执行失败，未全部下发（已执行 {len(done)}/{len(calls)} 项）：{exc}。"
                "已执行的部分以台账为准，请检查矿井接口（rescue-mine）后由指挥人员决定是否重新提交；不要自行重复提交相同内容之外的指令。"
            ) from exc
        not_done = [f"{r.kind.value}：{r.detail or r.status.value}" for r in done if r.status in (ActionStatus.REJECTED, ActionStatus.FAILED)]
        now = await mine_now() or datetime.now()
        receipt = ActionReceipt(
            action_id=action_id, tool=tool, issued_at=now, status="issued", note=note, mine_receipts=done, mine_not_executed=not_done
        )
        journal(log_entry(tool, "issued", payload, record_ids, **receipt.model_dump(mode="json", exclude={"tool", "status"})))
        log.info("action %s %s", tool, receipt.action_id)
        return receipt

    async def checked(tool: str, key: str, payload: BaseModel, record_id: str) -> dict[str, Any]:
        """预警、升级：与审批预检同一套核对（按记录），不通过就拒绝并记台账。"""
        now = await require_now(tool, payload, [record_id])
        result = precheck(tool, {key: payload.model_dump(mode="json")}, now, record_store)
        await reject_if(tool, payload, result["problems"], [record_id])
        return result

    def record_panel(record_id: str) -> str | None:
        try:
            return record_store.get(record_id).panel
        except (RecordNotFound, ValueError):
            return None

    # ── C 级对外动作（需要人工确认；DSH 审批插件拦截）──

    @server.tool()
    async def publish_warning(notice: WarningNotice) -> ActionReceipt:
        """发布自燃预警等级（确认人：通风部门负责人）。record_id 照抄 run_fire_assessment 返回的 record_ids.warning；
        等级必须与记录里的计算结果一致，人工改等级时在 override_reason 写明理由。"""
        result = await checked("publish_warning", "notice", notice, notice.record_id)
        request = NoticeRequest(
            kind=NoticeKind.WARNING,
            message=f"{notice.location}：{notice.summary}",
            panel=record_panel(notice.record_id),
            level=notice.level.value,
            idempotency_key=idempotency_key("publish_warning", notice),
        )
        return await deliver("publish_warning", notice, [notice.record_id], [lambda: md.send_notice(request)], "；".join(result["notes"]) or None)

    @server.tool()
    async def escalate_emergency(escalation: Escalation) -> ActionReceipt:
        """建议升级为应急响应（确认人：总工程师或矿长）。record_id 照抄 run_fire_assessment 返回的 record_ids.escalation，
        只有记录的结论为建议升级的才能提交。"""
        await checked("escalate_emergency", "escalation", escalation, escalation.record_id)
        request = NoticeRequest(
            kind=NoticeKind.INFO,
            message=f"建议升级为应急响应（{escalation.recommended_level}）：{escalation.reason}",
            panel=record_panel(escalation.record_id),
            level=escalation.recommended_level,
            idempotency_key=idempotency_key("escalate_emergency", escalation),
        )
        return await deliver("escalate_emergency", escalation, [escalation.record_id], [lambda: md.send_notice(request)])

    @server.tool()
    async def notify_evacuation(notice: EvacuationNotice) -> ActionReceipt:
        """发布补充撤人名单与避灾路线（确认人：调度员）。这是对预案撤人的补充，不替代、不延迟预案撤人。"""
        await reject_if("notify_evacuation", notice, await evacuation_problems(notice), [])
        where = {p.person_id: p.roadway_id for p in await md.personnel()}
        roadways = sorted({where[p] for p in notice.persons})
        notice_req = NoticeRequest(
            kind=NoticeKind.EVACUATION, message=notice.reason, targets=notice.persons, idempotency_key=idempotency_key("notify_evacuation", notice, "-notice")
        )
        calls = [lambda: md.send_notice(notice_req)]
        if roadways:
            treat = TreatmentRequest(type=StepType.EVACUATE, roadways=roadways, idempotency_key=idempotency_key("notify_evacuation", notice, "-evacuate"))
            calls.append(lambda: md.submit_treatment(treat))
        return await deliver("notify_evacuation", notice, [], calls)

    async def evacuation_problems(notice: EvacuationNotice) -> list[str]:
        """撤人名单按人员定位核对：名单里的人都在井下。取不到人员数据时不通过（fail closed）。"""
        try:
            where = {p.person_id for p in await md.personnel()}
        except Exception as exc:  # noqa: BLE001
            return [f"取不到井下人员定位，无法核对撤人名单：{exc}"]
        unknown = sorted(p for p in notice.persons if p not in where)
        return [f"名单里的人员不在井下人员定位中：{unknown}（已升井或编号有误）"] if unknown else []

    async def dispatch_problems(order: DispatchOrder) -> list[str]:
        """调度单按矿井数据核对：节点存在、物资在台账里、数量不超过台账库存。取不到矿井数据时不通过（fail closed）。"""
        try:
            nodes = {n.id for n in (await md.network()).nodes}
            stock: dict[str, float] = {}
            for item in await md.inventory():
                stock[item.item_type] = stock.get(item.item_type, 0.0) + item.quantity
        except Exception as exc:  # noqa: BLE001
            return [f"取不到矿井的网络或物资台账，无法核对调度单：{exc}"]
        problems = []
        want: dict[str, float] = {}
        for ln in order.lines:
            for n in (ln.from_node, ln.to_node):
                if n not in nodes:
                    problems.append(f"调度单里的节点 {n} 不在通风网络中")
            if ln.item_type not in stock:
                problems.append(f"物资类型 {ln.item_type} 不在应急物资台账中")
            want[ln.item_type] = want.get(ln.item_type, 0.0) + ln.quantity
        for t, q in want.items():
            if t in stock and q > stock[t]:
                problems.append(f"{t} 调度总量 {q:g} 超过台账库存 {stock[t]:g}")
        return list(dict.fromkeys(problems))

    @server.tool()
    async def issue_dispatch_order(order: DispatchOrder) -> ActionReceipt:
        """下发物资调度单（确认人：指挥部指定负责人）。明细按矿井的网络和物资台账核对（节点存在、物资在台账里、不超库存）。"""
        await reject_if("issue_dispatch_order", order, await dispatch_problems(order), [])
        request = DispatchRequest(purpose=order.purpose, lines=order.lines, idempotency_key=idempotency_key("issue_dispatch_order", order))
        return await deliver("issue_dispatch_order", order, [], [lambda: md.dispatch_order(request)])

    async def issue_plan_record(tool: str, record_id: str) -> ActionReceipt:
        class PlanRef(BaseModel):
            record_id: str

        ref = PlanRef(record_id=record_id)
        now = await require_now(tool, ref, [record_id])
        plan, problems, notes, _ = plan_from_record(tool, record_id, record_store, now)
        if plan is None:
            await reject_if(tool, ref, problems or ["方案记录核对未通过"], [record_id])
            raise ToolError("指令未发布。")  # reject_if 一定会抛；这里只为类型
        calls = []
        for step in sorted(plan.steps, key=lambda s: s.order):
            req = TreatmentRequest(
                panel=plan.panel, type=step.type, roadways=step.roadways, params=dict(step.params),
                idempotency_key=idempotency_key(tool, ref, f"-{step.order}"),
            )  # fmt: skip
            calls.append(lambda req=req: md.submit_treatment(req))
        # 启封方案的步骤顺序是安全要求（先检查、断电、撤人再启封）：前置步骤矿井没执行就不能继续
        return await deliver(tool, plan, [record_id], calls, "；".join(notes) or None, ordered=(tool == "approve_unsealing"))

    @server.tool()
    async def issue_sealing_plan(record_id: str) -> ActionReceipt:
        """下发封闭方案（确认人：总指挥 + 救护队带队指挥员）。只给 record_id，照抄 draft_sealing_plan 返回的 record_ids.review
        （合规审核通过的记录），不要提交方案内容——方案由本服务从记录里取，核对后执行强制红线校验，再按步骤交给矿井。"""
        return await issue_plan_record("issue_sealing_plan", record_id)

    @server.tool()
    async def issue_firefighting_plan(record_id: str) -> ActionReceipt:
        """下发防灭火处置方案（确认人：总工程师；不封闭火区、不安排人员进入灾区）。只给 record_id，照抄 draft_firefighting_plan
        返回的 record_ids.review（合规审核通过的记录），不要提交方案内容——方案由本服务从记录里取，核对后执行强制红线校验，再按步骤交给矿井。"""
        return await issue_plan_record("issue_firefighting_plan", record_id)

    @server.tool()
    async def approve_unsealing(record_id: str) -> ActionReceipt:
        """批准启封（确认人：总指挥）。只给 record_id，照抄 assess_unsealing 返回的 record_ids.unseal（启封评估记录，结论必须是
        “具备启封条件”）；不要提交方案内容——方案由本服务从记录里取，核对启封条件、时效和红线（第164条：先检查、断电、撤人）后，
        再按步骤交给矿井。"""
        return await issue_plan_record("approve_unsealing", record_id)

    # ── A 级只读：方案红线自查（审批插件按动作分级放行，不弹确认）──

    @server.tool()
    def precheck_plan(tool: str, plan: Plan) -> dict[str, Any]:
        """方案红线自查（只读，不发布、不弹确认）。tool 取 issue_sealing_plan、issue_firefighting_plan 或 approve_unsealing。只查方案类型和红线，不检查数据时效，
        也不核对记录（发布时按审核记录核对，并强制再校验一次）。
        返回 ok 和问题清单（含条号）、检查了哪些规则（rules_checked_count 照抄，不要自己数）。
        规则所依据的条文原文用 kb_get_articles（document="矿山救援规程"，articles 取 rule_articles）获取。"""
        problems = validate_plan(tool, plan, None)
        return {
            "ok": not problems,
            "problems": problems,
            "notes": [],
            "rules_checked": [f"{r.rule_id} {r.article} {r.summary}" for r in RULES],
            "rules_checked_count": len(RULES),
            "rule_articles": sorted({f"第{n}条" for r in RULES for n in re.findall(r"\d+", r.article)}, key=lambda a: int(a[1:-1])),
        }

    # ── A 级只读：台账查询 ──

    @server.tool()
    def query_last_issued(panel: str = "1201") -> dict[str, Any]:
        """本工作面最近一次已下发（status=issued）的封闭方案或防灭火方案（只读）。固定流程 review_effect 缺处置开始时间时用它，
        found 为 false 时没有可用的下发记录。返回 tool、action_id、issued_at（矿井时间）、plan_id、title。"""
        hits = [
            r
            for r in ledger_records(ledger.path)
            if r.get("tool") in ("issue_sealing_plan", "issue_firefighting_plan") and r.get("status") == "issued" and for_panel(r.get("payload") or {}, panel)
        ]
        if not hits:
            return {"found": False, "panel": panel}
        r = hits[-1]
        p = r.get("payload") or {}
        return {"found": True, "panel": panel, "tool": r["tool"], "action_id": r.get("action_id"), "issued_at": str(r.get("issued_at")), "plan_id": p.get("plan_id"), "title": p.get("title")}

    # ── DSH 审批插件的预检接口（HTTP，不是 MCP 工具）──

    @server.custom_route("/redlines/check", methods=["POST"])
    async def redlines_check(req: Request) -> JSONResponse:
        body = await req.json()
        tool = body.get("tool", "")
        now = await mine_now()
        if now is None and tool in TIMED_TOOLS:  # 撤人名单、调度单不核对时效，时钟故障时不能拦
            return JSONResponse({"ok": False, "problems": [NO_CLOCK], "notes": []})
        result = precheck(tool, body.get("arguments") or {}, now, record_store)
        args = body.get("arguments") or {}
        if tool in ("issue_dispatch_order", "notify_evacuation") and result["ok"]:  # 按矿井数据核对（人工确认之前就拦住）
            try:
                if tool == "issue_dispatch_order":
                    problems = await dispatch_problems(DispatchOrder.model_validate(args.get("order", args)))
                else:
                    problems = await evacuation_problems(EvacuationNotice.model_validate(args.get("notice", args)))
            except Exception as exc:  # noqa: BLE001
                problems = [f"指令格式不符合数据契约：{exc}"]
            if problems:
                result = {**result, "ok": False, "problems": problems}
        if isinstance(result.get("summary"), dict):  # 审批超时升级要挂在事件上：摘要带上事件编号
            result["summary"]["incident_id"] = incident_of(result["summary"].get("record_ids") or [])
        return JSONResponse(result)

    return server
