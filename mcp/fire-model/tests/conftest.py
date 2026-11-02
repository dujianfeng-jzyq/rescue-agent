import pytest


@pytest.fixture(autouse=True)
def _chart_dir(tmp_path, monkeypatch):
    """爆炸三角形工具每次都会存风险图，测试里写到临时目录。"""
    monkeypatch.setenv("RESCUE_CHART_DIR", str(tmp_path / "charts"))
