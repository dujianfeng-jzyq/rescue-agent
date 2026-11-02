"""W8 复盘时间线：把指令台账、值守事件、研判快照合成按矿井时间排序的时间线；审计日志按系统时间单列。

审计里 detail 为 “the user rejected tool …” 的记录原样标出，不推断是谁拒绝的：DSH 在审批策略为 never 时
（如委派出的子会话、Full access）也会这样记录（验证清单 #18、Full access 一节）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from rescue_contract import ledger
from rescue_contract.snapshots import SnapshotStore

from .monitor import read_events

ACTION_NAMES = {
    "publish_warning": "发布预警等级", "issue_firefighting_plan": "下发防灭火方案", "escalate_emergency": "升级为应急响应",
    "notify_evacuation": "发布补充撤人名单", "issue_dispatch_order": "下发物资调度单", "issue_sealing_plan": "下发封闭方案",
    "approve_unsealing": "批准启封",
}  # fmt: skip
STATUS = {"issued": "已发布", "rejected": "被服务端校验拒绝"}


def audit_path() -> Path:
    return Path(os.environ.get("RESCUE_AUDIT_LOG") or Path(os.environ.get("RESCUE_HOME", ".")) / "var" / "audit" / "tool-actions.jsonl")


def _summary(kind: str, r: dict[str, Any]) -> str:
    if kind == "assessment":
        return f"研判：{r['warning']['level_name']}，{r['warning']['stage_name']}"
    if kind == "effect":
        return f"效果评估：{r['overall_name']}"
    if kind == "escalation":
        return "升级建议：" + ("建议升级" if r["recommend"] else "暂不建议")
    if kind == "plan-context":
        return f"方案草案 {r['plan']['plan_id']}"
    return f"调度草案（方案 {r.get('plan_id')}）"


def _in(t: datetime, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or t >= start) and (end is None or t <= end)


def build(panel: str, store: SnapshotStore, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for r in ledger.records():
        t = r.get("issued_at") or r.get("at")
        if not t:
            continue
        at = datetime.fromisoformat(str(t))
        payload = r.get("payload") or {}
        if not _in(at, start, end) or not ledger.for_panel(payload, panel) and payload.get("location", "").find(panel) < 0:
            continue
        name = ACTION_NAMES.get(r.get("tool", ""), r.get("tool", ""))
        detail = payload.get("plan_id") or payload.get("order_id") or payload.get("level") or ""
        msg = f"{name} {detail}：{STATUS.get(r.get('status'), r.get('status'))}"
        if r.get("problems"):
            msg += "（" + "；".join(str(p).splitlines()[0] for p in r["problems"]) + "）"
        items.append({"mine_time": at.isoformat(), "source": "指令台账", "event": msg, "ref": r.get("action_id")})
    for e in read_events(panel=panel):
        at = datetime.fromisoformat(e["mine_time"])
        if _in(at, start, end):
            items.append({"mine_time": e["mine_time"], "source": "值守", "event": e["message"], "ref": e.get("snapshot_id")})
    if store.dir.is_dir():
        for path in sorted(store.dir.glob("SN-*.json")):
            snap = json.loads(path.read_text(encoding="utf-8"))
            at = datetime.fromisoformat(snap["as_of"])
            if snap["panel"] != panel or not _in(at, start, end):
                continue
            summary = _summary(snap["kind"], snap["result"])
            items.append({"mine_time": snap["as_of"], "source": "研判快照", "event": summary, "ref": snap["snapshot_id"]})
    items.sort(key=lambda x: (x["mine_time"], x["source"]))
    audits = []
    path = audit_path()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                a = json.loads(line)
            except ValueError:
                continue
            note = None
            if "the user rejected tool" in str(a.get("detail") or ""):
                note = "审批记录为“用户拒绝”：可能是确认人点了拒绝，也可能是审批策略为 never 时被自动拒绝，需人工核对"
            audits.append(
                {"system_time": a.get("ts"), "tool": a.get("tool"), "outcome": a.get("outcome"), "detail": a.get("detail"), "note": note}
            )
    return {
        "panel": panel,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "timeline": items,
        "audit": audits,
    }
