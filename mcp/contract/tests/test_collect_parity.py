"""采集一致性：证据插件（TS）与 Python 采集小工具对同一时刻的模拟矿井按同一份清单采集，规范化 JSON 逐字节相同。

起一个手动时钟的模拟矿井（子进程，随机端口），推进到场景里有变化的时刻；TS 侧用 dsh-bundle/tools/collect.ts。
没有 node 或 dsh-bundle 依赖时跳过（CI 的 Python 任务里没有 node）。
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from rescue_contract.canonical import canonical_json, content_sha256
from rescue_contract.collect import collect_from, minus_hours, parse_collect_spec

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "dsh-bundle"

SPEC = """
status: placeholder
reviewed_by: null
kinds:
  check:
    sensors:
      - name: tubes
        select: {source: bundle_tube, panel: true}
        require_measurands: [O2, CO2]
        window_h: 6
        required: true
      - name: upper_corner
        select: {ids: ["SM-{panel}-UC"]}
        measurands: [CO, CH4]
        required: true
      - name: nothing
        select: {ids: ["NO-SUCH-{panel}"]}
        required: true
    events_h: 6
    fire_areas: true
    personnel: true
  effect:
    sensors:
      - name: tubes
        select: {source: bundle_tube, panel: true}
        measurands: [CO, C2H4]
        before_since_h: 12
"""


def test_minus_hours():
    assert minus_hours("2026-11-07T08:00:00.123456", 6) == "2026-11-07T02:00:00"
    assert minus_hours("2026-11-07T08:00", 0.25) == "2026-11-07T07:45:00"


def test_spec_validation():
    with pytest.raises(ValueError, match="status"):
        parse_collect_spec("kinds: {}\n")
    with pytest.raises(ValueError, match="name 和 select"):
        parse_collect_spec("status: placeholder\nkinds:\n  check:\n    sensors:\n      - name: x\n")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


@pytest.fixture(scope="module")
def sim_url():
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESCUE_MCP_TOKEN")}
    env["RESCUE_MINE_DATA_PORT"] = str(port)
    proc = subprocess.Popen([sys.executable, "-m", "rescue_mine_data_sim"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            if proc.poll() is not None:
                raise RuntimeError(f"模拟矿井启动失败（退出码 {proc.returncode}）")
            try:
                _post(f"{base}/sim/scenario", {"id": "s4-escalation", "manual": True})
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("模拟矿井 15 秒内没有就绪")
        _post(f"{base}/sim/advance", {"hours": 40})
        yield f"{base}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.skipif(shutil.which("node") is None or not (BUNDLE / "node_modules" / "yaml").exists(), reason="没有 node 或 dsh-bundle 依赖")
def test_ts_and_python_collect_identically(sim_url, tmp_path, monkeypatch):
    monkeypatch.delenv("RESCUE_MCP_TOKEN", raising=False)
    spec_path = tmp_path / "collect.yaml"
    spec_path.write_text(SPEC, encoding="utf-8")
    py = asyncio.run(collect_from(sim_url, parse_collect_spec(SPEC), "check", "1201"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESCUE_MCP_")}
    out = subprocess.run(["node", "tools/collect.ts", str(spec_path), "check", "1201", sim_url], cwd=BUNDLE, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    ts = json.loads(out.stdout)

    # 先确认采到了东西，再比对
    assert py["groups"]["tubes"]["sensors"] and py["groups"]["tubes"]["readings"] and py["groups"]["upper_corner"]["latest"]
    assert py["missing"] == [{"group": "nothing", "reason": "没有匹配的测点"}]
    assert ts["canonical"] == canonical_json(py)
    assert ts["content_sha256"] == content_sha256(py)


@pytest.mark.skipif(shutil.which("node") is None or not (BUNDLE / "node_modules" / "yaml").exists(), reason="没有 node 或 dsh-bundle 依赖")
def test_ts_and_python_collect_since_window_identically(sim_url, tmp_path, monkeypatch):
    """复评的窗口按处置开始时间取（before_since_h），两边算出的窗口和读数相同。"""
    monkeypatch.delenv("RESCUE_MCP_TOKEN", raising=False)
    spec_path = tmp_path / "collect.yaml"
    spec_path.write_text(SPEC, encoding="utf-8")
    now = asyncio.run(collect_from(sim_url, parse_collect_spec(SPEC), "check", "1201"))["as_of"]
    since = minus_hours(now, 7.25)
    py = asyncio.run(collect_from(sim_url, parse_collect_spec(SPEC), "effect", "1201", since))
    assert py["since"] == since and py["groups"]["tubes"]["window_h"] == 7.25 + 12
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESCUE_MCP_")}
    out = subprocess.run(["node", "tools/collect.ts", str(spec_path), "effect", "1201", sim_url, since], cwd=BUNDLE, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["canonical"] == canonical_json(py)


def test_since_window_needs_since(sim_url):
    with pytest.raises(BaseException) as info:  # MCP 客户端的任务组把它包成 ExceptionGroup
        asyncio.run(collect_from(sim_url, parse_collect_spec(SPEC), "effect", "1201"))
    assert "需要给出 since" in repr(info.value) or "需要给出 since" in str(getattr(info.value, "exceptions", ""))
