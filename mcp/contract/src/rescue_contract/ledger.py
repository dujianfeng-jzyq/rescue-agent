"""指令台账（actions 服务写入的 JSONL）的只读访问：workflow 取处置开始时间、复盘时间线用。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

LEDGER_ENV = "RESCUE_ACTIONS_LEDGER"


def ledger_path() -> Path:
    return Path(os.environ.get(LEDGER_ENV) or Path(os.environ.get("RESCUE_HOME", ".")) / "var" / "actions-ledger.jsonl")


def records(path: Path | None = None) -> list[dict[str, Any]]:
    """全部记录（按写入顺序）；文件不存在时为空。读不懂的行跳过（台账只追加，不在这里修复）。"""
    p = path or ledger_path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def for_panel(payload: dict[str, Any], panel: str) -> bool:
    if payload.get("panel") is not None:
        return payload["panel"] == panel
    return panel in str(payload.get("title", "")) or any(panel in r for s in payload.get("steps", []) for r in s.get("roadways", []))


def last_issued(tool: str, panel: str, path: Path | None = None) -> dict[str, Any] | None:
    """本工作面最近一次发布成功的某类指令（status=issued）。"""
    hits = [r for r in records(path) if r.get("tool") == tool and r.get("status") == "issued" and for_panel(r.get("payload") or {}, panel)]
    return hits[-1] if hits else None


def issued_at(record: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(record["issued_at"]))
