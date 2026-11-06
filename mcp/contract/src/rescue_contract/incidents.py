"""事件（Incident）库（第三版 P3，docs/design-v3.md 7.2）：文件存储，监听器（Python）和事件插件（DSH 进程里的 TS）共写。

目录 RESCUE_INCIDENT_DIR（缺省 $RESCUE_HOME/var/incidents）：
- `<incident_id>.jsonl`：一个事件一个文件，**只追加**，每行一条时间线条目；事件的状态、待确认项、跟踪计划都是从条目折叠出来的（fold）。
- `sentinel/`：监听器自己的状态和心跳（不属于事件）。
- `duty-session.json`：事件插件记的值班会话编号。

条目：{event_id, at（矿井时间）, wall（系统时间，仅审计）, kind, by, data}。event_id 全局唯一，是“至少一次”投递时智能体去重的依据。
写入方：监听器（by=sentinel）、事件插件（by=agent、plugin）、控制台不写。一次追加是一次 O_APPEND 写，小于管道原子写上限时不会交错；
创建事件用 O_EXCL。TS 侧（dsh-bundle/src/incident/store.ts）格式和折叠规则相同，共用 tests/fixtures/incidents 的样例。

条目的 kind：
- opened      开事件（data: category, panel, summary）
- alert       需要智能体/操作员处理的告警（data: cause, summary, reasons, record 摘要…）；“未确认”= 还没有 delivered 或 acked 引用它
- recovered   监测恢复正常（data: summary）
- degraded    监听降级（取数连续失败）；恢复时写 recovered（data.of = degraded）
- delivered   事件插件已把告警投递给值班会话（data: alert_id, session_id）
- delivery_failed  投递失败（data: alert_id, error, attempts）
- acked       操作员/智能体确认已看到（data: alert_id）
- status      状态变化（data: status ∈ monitoring/handling/stable/closed, reason）
- followup_added / followup_done / followup_due   跟踪计划（data: followup_id, due_at, expect…）
- note        其他时间线信息（研判、对外动作、人工输入等，data: type, text, record_ids…）。type 为 approval_pending / approval_decided / approval_escalated 的
              note 记录人工确认的等待、结果和超时升级（approval_key 配对）；未决的折叠成 pending_approvals
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

INCIDENT_DIR_ENV = "RESCUE_INCIDENT_DIR"
STATUSES = ("monitoring", "handling", "stable", "closed")
STATUS_NAMES = {"monitoring": "监测中", "handling": "处置中", "stable": "已稳定", "closed": "已关闭"}
KINDS = (
    "opened", "alert", "recovered", "degraded", "delivered", "delivery_failed", "acked", "status",
    "followup_added", "followup_done", "followup_due", "note",
)  # fmt: skip
CATEGORIES = ("fire", "monitor")  # 火情；监听自身降级（取数失败）
_ID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


class IncidentNotFound(KeyError):
    pass


def _rand(n: int = 6) -> str:
    return "".join(secrets.choice(_ID_CHARS) for _ in range(n))


def mine_stamp(at: str) -> str:
    return datetime.fromisoformat(at[:19]).strftime("%Y%m%dT%H%M%S")


def new_incident_id(at: str) -> str:
    return f"INC-{mine_stamp(at)}-{_rand()}"


def fold(incident_id: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """把条目折叠成事件视图。规则与 TS 侧相同（顺序即文件顺序）。"""
    if not entries or entries[0]["kind"] != "opened":
        raise ValueError(f"事件 {incident_id} 的第一条应为 opened")
    first = entries[0]["data"]
    status = "monitoring"
    alerts: dict[str, dict[str, Any]] = {}
    handled: set[str] = set()
    followups: dict[str, dict[str, Any]] = {}
    degraded = False
    approvals: dict[str, dict[str, Any]] = {}
    for e in entries:
        k, d = e["kind"], e.get("data") or {}
        if k == "alert":
            alerts[e["event_id"]] = e
            if status == "stable":
                status = "monitoring"  # 稳定后再次告警：回到监测中
        elif k in ("delivered", "acked"):
            handled.add(d.get("alert_id"))
        elif k == "status":
            if d.get("status") not in STATUSES:
                raise ValueError(f"事件 {incident_id}：未知状态 {d.get('status')}")
            status = d["status"]
        elif k == "followup_added":
            followups[d["followup_id"]] = {**d, "added_at": e["at"], "state": "pending"}
        elif k == "followup_due" and d.get("followup_id") in followups:
            followups[d["followup_id"]]["state"] = "due"
        elif k == "followup_done" and d.get("followup_id") in followups:
            followups[d["followup_id"]]["state"] = "done"
        elif k == "note" and d.get("type") == "approval_pending":
            approvals[d["approval_key"]] = {**d, "asked_wall": e["wall"], "escalated": False}
        elif k == "note" and d.get("type") == "approval_decided":
            approvals.pop(d.get("approval_key"), None)
        elif k == "note" and d.get("type") == "approval_escalated" and d.get("approval_key") in approvals:
            approvals[d["approval_key"]]["escalated"] = True
        elif k == "degraded":
            degraded = True
        elif k == "recovered" and d.get("of") == "degraded":
            degraded = False
    last = entries[-1]
    return {
        "incident_id": incident_id,
        "category": first["category"],
        "panel": first.get("panel"),
        "status": status,
        "status_name": STATUS_NAMES[status],
        "opened_at": entries[0]["at"],
        "updated_at": last["at"],
        "summary": first.get("summary", ""),
        "degraded": degraded,
        "pending_alerts": [a["event_id"] for a in alerts.values() if a["event_id"] not in handled],
        "followups": list(followups.values()),
        "pending_approvals": list(approvals.values()),
        "entry_count": len(entries),
    }


class IncidentStore:
    def __init__(self, directory: Path | str | None = None) -> None:
        default = Path(os.environ.get("RESCUE_HOME", ".")) / "var" / "incidents"
        self.dir = Path(directory or os.environ.get(INCIDENT_DIR_ENV) or default)

    def _path(self, incident_id: str) -> Path:
        if not incident_id.startswith("INC-") or "/" in incident_id or "\\" in incident_id or ".." in incident_id:
            raise IncidentNotFound(f"事件编号不合法：{incident_id}")
        return self.dir / f"{incident_id}.jsonl"

    @staticmethod
    def _line(at: str, kind: str, by: str, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if kind not in KINDS:
            raise ValueError(f"未知的条目类型 {kind}")
        entry = {
            "event_id": f"EVT-{mine_stamp(at)}-{_rand()}",
            "at": at[:19],
            "wall": datetime.now(UTC).isoformat(timespec="seconds"),
            "kind": kind,
            "by": by,
            "data": data,
        }
        return json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n", entry

    def open(self, at: str, *, category: str, panel: str | None, summary: str, by: str = "sentinel") -> str:
        if category not in CATEGORIES:
            raise ValueError(f"未知的事件类别 {category}")
        self.dir.mkdir(parents=True, exist_ok=True)
        line, _ = self._line(at, "opened", by, {"category": category, "panel": panel, "summary": summary})
        for _ in range(5):
            incident_id = new_incident_id(at)
            try:
                fd = os.open(self._path(incident_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(line)
            return incident_id
        raise RuntimeError("生成事件编号失败")

    def append(self, incident_id: str, at: str, kind: str, by: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        path = self._path(incident_id)
        if not path.is_file():
            raise IncidentNotFound(f"找不到事件 {incident_id}")
        line, entry = self._line(at, kind, by, data or {})
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))  # 一次写入一整行
        finally:
            os.close(fd)
        return entry

    def entries(self, incident_id: str) -> list[dict[str, Any]]:
        path = self._path(incident_id)
        if not path.is_file():
            raise IncidentNotFound(f"找不到事件 {incident_id}")
        out = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue  # 写到一半崩溃留下的残行：跳过，不让它挡住整个事件
        return out

    def get(self, incident_id: str) -> dict[str, Any]:
        return fold(incident_id, self.entries(incident_id))

    def ids(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("INC-*.jsonl")) if self.dir.is_dir() else []

    def list(self, *, include_closed: bool = False) -> list[dict[str, Any]]:
        out = []
        for i in self.ids():
            try:
                v = self.get(i)
            except (ValueError, KeyError, IncidentNotFound):
                continue
            if include_closed or v["status"] != "closed":
                out.append(v)
        return out

    def find_open(self, category: str, panel: str | None) -> dict[str, Any] | None:
        """同类别、同工作面的未关闭事件（最近更新的一个）。"""
        found = [v for v in self.list() if v["category"] == category and v["panel"] == panel]
        return max(found, key=lambda v: v["updated_at"]) if found else None

    def pending_alerts(self) -> list[dict[str, Any]]:
        """所有事件里还没投递也没确认的告警条目，附事件编号，按时间排序。"""
        out = []
        for v in self.list():
            if not v["pending_alerts"]:
                continue
            want = set(v["pending_alerts"])
            out += [{**e, "incident_id": v["incident_id"], "panel": v["panel"]} for e in self.entries(v["incident_id"]) if e["event_id"] in want]
        return sorted(out, key=lambda e: (e["at"], e["event_id"]))
