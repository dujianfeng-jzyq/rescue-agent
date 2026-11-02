import sys
from pathlib import Path

import pytest
from rescue_contract.snapshots import SnapshotStore

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "eval" / "agent"))


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    """风险图、快照、台账都写到临时目录。"""
    monkeypatch.setenv("RESCUE_CHART_DIR", str(tmp_path / "charts"))
    monkeypatch.setenv("RESCUE_SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setenv("RESCUE_ACTIONS_LEDGER", str(tmp_path / "ledger.jsonl"))


@pytest.fixture
def store(tmp_path):
    return SnapshotStore(tmp_path / "snapshots")
