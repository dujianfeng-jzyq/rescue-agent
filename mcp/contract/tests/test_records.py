"""记录模型与存储：编号格式、类型前缀、哈希核对、不可覆盖。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from rescue_contract.canonical import content_sha256
from rescue_contract.models import Origin, Record, RecordKind
from rescue_contract.records import RecordNotFound, RecordStore, RecordTampered, new_record_id, verify

AS_OF = datetime(2026, 11, 7, 8, 0, 0)
ORIGIN = Origin(channel="web", trigger="chat", caller="调度员", conversation_id="C-1")


def make(kind=RecordKind.RESULT, content=None, **kw) -> Record:
    content = {"level": "yellow", "co_ppm": 46.7} if content is None else content
    data = dict(
        record_id=new_record_id(kind, AS_OF),
        kind=kind,
        panel="1201",
        as_of=AS_OF,
        created_at=datetime(2026, 11, 3, 14, 0, tzinfo=UTC),
        origin=ORIGIN,
        source={"skill": "fire-assessment", "script": "warning.py"},
        content=content,
        content_sha256=content_sha256(content),
    )
    data.update(kw)
    return Record(**data)


def test_new_record_id_format():
    rid = new_record_id(RecordKind.EVIDENCE, AS_OF)
    assert rid.startswith("EV-20261107T080000-") and len(rid) == len("EV-20261107T080000-") + 6


def test_prefix_must_match_kind():
    with pytest.raises(ValidationError, match="前缀"):
        make(kind=RecordKind.REPORT, record_id=new_record_id(RecordKind.RESULT, AS_OF))


@pytest.mark.parametrize("bad", ["RS-1", "XX-20261107T080000-abcdef", "RS-20261107T080000-ABCDEF", "../RS-20261107T080000-abcdef"])
def test_record_id_pattern(bad):
    with pytest.raises(ValidationError):
        make(record_id=bad)


def test_created_at_needs_timezone_and_as_of_must_not_have_one():
    with pytest.raises(ValidationError):
        make(created_at=datetime(2026, 11, 3, 14, 0))
    with pytest.raises(ValidationError):
        make(as_of=datetime(2026, 11, 7, 8, 0, tzinfo=UTC))


def test_string_content():
    r = make(kind=RecordKind.REPORT, content="# 研判报告\n预警等级：yellow")
    assert verify(r) is r


def test_store_roundtrip_and_no_overwrite(tmp_path):
    store = RecordStore(tmp_path)
    r = store.put(make())
    assert store.get(r.record_id) == r
    with pytest.raises(ValueError, match="不能覆盖"):
        store.put(r)
    assert not list(tmp_path.glob(".*.tmp"))


def test_put_rejects_wrong_hash(tmp_path):
    with pytest.raises(RecordTampered):
        RecordStore(tmp_path).put(make(content_sha256="0" * 64))


def test_get_detects_tampering(tmp_path):
    store = RecordStore(tmp_path)
    r = store.put(make())
    path = tmp_path / f"{r.record_id}.json"
    path.write_text(path.read_text(encoding="utf-8").replace("46.7", "16.7"), encoding="utf-8")
    with pytest.raises(RecordTampered):
        store.get(r.record_id)


@pytest.mark.parametrize("rid", ["RS-20261107T080000-zzzzzz", "../etc/passwd", ".hidden"])
def test_get_missing_or_bad_id(tmp_path, rid):
    with pytest.raises(RecordNotFound):
        RecordStore(tmp_path).get(rid)


def test_store_dir_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RESCUE_RECORD_DIR", str(tmp_path / "r"))
    assert RecordStore().dir == tmp_path / "r"
