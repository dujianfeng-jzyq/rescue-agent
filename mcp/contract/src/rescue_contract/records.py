"""记录的存储：证据插件（DSH 进程）写入；actions、审批预检、Python 采集小工具按编号读取。

文件存储，每条记录一个 JSON 文件，目录 RESCUE_RECORD_DIR（缺省 $RESCUE_HOME/var/records）。
只新增、不修改、不删除：同一编号再次写入视为错误。读取时重算 content_sha256，不一致视为被篡改。

哈希只覆盖 content：source（技能、脚本、上游记录）、kind、panel、as_of 不在哈希里，改了查不出来，而指令核对要用到它们。
所以哈希只能发现内容被误改；防篡改靠写权限：记录目录只有证据插件（DSH 进程）能写，沙箱、guard、模拟矿井只读
（Docker 里只读挂载）。对接真实矿井前考虑给整条记录加哈希或签名（2026-11-11 代码复查第 2 轮）。
写入方是 TypeScript 的证据插件；这里的 put 给 Python 采集小工具（不经 DSH 的基线评测）和测试用，格式与 TS 侧相同。
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path

from .canonical import content_sha256
from .models import RECORD_PREFIX, Record, RecordKind

RECORD_DIR_ENV = "RESCUE_RECORD_DIR"
_ID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


class RecordNotFound(KeyError):
    pass


class RecordTampered(ValueError):
    """记录内容与 content_sha256 不符。"""


def new_record_id(kind: RecordKind, as_of: datetime) -> str:
    """<EV|RS|RP>-<矿井时间>-<6 位随机>。"""
    return f"{RECORD_PREFIX[kind]}-{as_of:%Y%m%dT%H%M%S}-{''.join(secrets.choice(_ID_CHARS) for _ in range(6))}"


def verify(record: Record) -> Record:
    actual = content_sha256(record.content)
    if actual != record.content_sha256:
        raise RecordTampered(f"记录 {record.record_id} 的内容与 content_sha256 不符（记录 {record.content_sha256[:12]}…，重算 {actual[:12]}…）")
    return record


class RecordStore:
    def __init__(self, directory: Path | str | None = None) -> None:
        default = Path(os.environ.get("RESCUE_HOME", ".")) / "var" / "records"
        self.dir = Path(directory or os.environ.get(RECORD_DIR_ENV) or default)

    def _path(self, record_id: str) -> Path:
        # 编号格式由 Record 的 pattern 约束；这里再挡一次路径穿越
        if "/" in record_id or "\\" in record_id or record_id.startswith("."):
            raise RecordNotFound(f"记录编号不合法：{record_id}")
        return self.dir / f"{record_id}.json"

    def put(self, record: Record) -> Record:
        verify(record)
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(record.record_id)
        tmp = path.with_name(f".{record.record_id}.{secrets.token_hex(4)}.tmp")
        tmp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        try:
            os.link(tmp, path)  # 目标已存在时失败：不覆盖
        except FileExistsError:
            raise ValueError(f"记录已存在，不能覆盖：{record.record_id}") from None
        finally:
            tmp.unlink(missing_ok=True)
        return record

    def get(self, record_id: str) -> Record:
        path = self._path(record_id)
        if not path.is_file():
            raise RecordNotFound(f"找不到记录 {record_id}")
        return verify(Record.model_validate_json(path.read_text(encoding="utf-8")))
