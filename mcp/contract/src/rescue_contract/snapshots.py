"""研判快照的存储：workflow 服务写入，actions、审批预检和 kb 自查按编号读取核对。

目前是文件存储（每个快照一个 JSON 文件），要求这几个服务共享同一个目录；分机部署时换成数据库。
只新增、不修改、不删除：同一编号再次写入视为错误。
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path

from .models import Snapshot, SnapshotKind

SNAPSHOT_DIR_ENV = "RESCUE_SNAPSHOT_DIR"
_ID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


class SnapshotNotFound(KeyError):
    pass


def new_id(as_of: datetime) -> str:
    """SN-<矿井时间>-<6 位随机>，按时间可排序，不可猜测。"""
    return f"SN-{as_of:%Y%m%dT%H%M%S}-{''.join(secrets.choice(_ID_CHARS) for _ in range(6))}"


def _valid_id(snapshot_id: str) -> bool:
    return snapshot_id.startswith("SN-") and len(snapshot_id) <= 40 and all(c.isalnum() or c in "-" for c in snapshot_id)


class SnapshotStore:
    def __init__(self, directory: Path | str | None = None) -> None:
        default = Path(os.environ.get("RESCUE_HOME", ".")) / "var" / "snapshots"
        self.dir = Path(directory or os.environ.get(SNAPSHOT_DIR_ENV) or default)

    def put(self, snap: Snapshot) -> Snapshot:
        if not _valid_id(snap.snapshot_id):
            raise ValueError(f"快照编号不合法：{snap.snapshot_id}")
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{snap.snapshot_id}.json"
        if path.exists():
            raise ValueError(f"快照已存在，不能覆盖：{snap.snapshot_id}")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(snap.model_dump_json(indent=2), encoding="utf-8")
        tmp.rename(path)
        return snap

    def get(self, snapshot_id: str) -> Snapshot:
        path = self.dir / f"{snapshot_id}.json"
        if not _valid_id(snapshot_id) or not path.is_file():
            raise SnapshotNotFound(f"找不到快照 {snapshot_id}")
        return Snapshot.model_validate_json(path.read_text(encoding="utf-8"))

    def latest(self, kind: SnapshotKind, panel: str) -> Snapshot | None:
        """按编号（矿井时间）取最新的一个；同一时刻多个时取文件最新写入的。"""
        if not self.dir.is_dir():
            return None
        best: tuple[str, float, Snapshot] | None = None
        for path in self.dir.glob("SN-*.json"):
            snap = Snapshot.model_validate_json(path.read_text(encoding="utf-8"))
            if snap.kind != kind or snap.panel != panel:
                continue
            key = (snap.snapshot_id[3:18], path.stat().st_mtime)
            if best is None or key > best[:2]:
                best = (*key, snap)
        return best[2] if best else None
