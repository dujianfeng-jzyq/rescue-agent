"""事件库：折叠规则与 TS 侧共用样例（dsh-bundle/test/incident-store.test.ts 读同一目录）、追加、合并、容错。"""

import json
import shutil
from pathlib import Path

import pytest
from rescue_contract.incidents import IncidentNotFound, IncidentStore

FIX = Path(__file__).parent / "fixtures" / "incidents"


def test_fold_matches_shared_fixture():
    store = IncidentStore(FIX)
    want = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))
    assert {i: store.get(i) for i in store.ids()} == want


def test_truncated_line_is_skipped(tmp_path):
    shutil.copytree(FIX, tmp_path / "x")
    store = IncidentStore(tmp_path / "x")
    assert len(store.entries("INC-20261105T090000-bbbbbb")) == 4


def test_open_append_and_find(tmp_path):
    s = IncidentStore(tmp_path)
    iid = s.open("2026-11-05T08:00:00", category="fire", panel="1201", summary="x")
    assert s.find_open("fire", "1201")["incident_id"] == iid
    assert s.find_open("fire", "1202") is None and s.find_open("monitor", "1201") is None
    a = s.append(iid, "2026-11-05T08:10:00", "alert", "sentinel", {"summary": "s"})
    assert s.get(iid)["pending_alerts"] == [a["event_id"]]
    assert [p["event_id"] for p in s.pending_alerts()] == [a["event_id"]]
    s.append(iid, "2026-11-05T08:11:00", "delivered", "plugin", {"alert_id": a["event_id"]})
    assert s.get(iid)["pending_alerts"] == [] and s.pending_alerts() == []
    s.append(iid, "2026-11-05T09:00:00", "status", "agent", {"status": "closed"})
    assert s.find_open("fire", "1201") is None and s.list() == [] and len(s.list(include_closed=True)) == 1


def test_rejects_bad_input(tmp_path):
    s = IncidentStore(tmp_path)
    with pytest.raises(IncidentNotFound):
        s.get("../etc/passwd")
    with pytest.raises(IncidentNotFound):
        s.append("INC-20261105T080000-zzzzzz", "2026-11-05T08:00:00", "note", "agent")
    iid = s.open("2026-11-05T08:00:00", category="fire", panel=None, summary="x")
    with pytest.raises(ValueError):
        s.append(iid, "2026-11-05T08:00:00", "bogus", "agent")
    s.append(iid, "2026-11-05T08:00:00", "status", "agent", {"status": "weird"})
    with pytest.raises(ValueError):
        s.get(iid)  # 未知状态：折叠失败，列表里跳过这个事件而不是崩溃
    assert s.list() == []
