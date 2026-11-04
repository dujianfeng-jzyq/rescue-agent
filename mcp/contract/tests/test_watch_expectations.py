"""第二版 P4 验收：持续检测（check_mine watch 的核心 src/evidence/watch.ts，不经模型）的结束时刻和原因符合
eval/expectations.yaml 的 watch 期望。手动时钟的模拟矿井从第 0 小时开始，每个检测周期推进一次（dsh-bundle/tools/watch.ts）。

没有 node 或 dsh-bundle 依赖时跳过。s1 跑满 72 矿井小时（432 个周期），约 1 分钟。
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "dsh-bundle"
EXPECT = yaml.safe_load((ROOT / "eval" / "expectations.yaml").read_text(encoding="utf-8"))
CASES = [k for k, v in EXPECT.items() if isinstance(v, dict) and "watch" in v]
NEED_NODE = pytest.mark.skipif(shutil.which("node") is None or not (BUNDLE / "node_modules" / "yaml").exists(), reason="没有 node 或 dsh-bundle 依赖")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


@pytest.fixture
def sim():
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESCUE_MCP_TOKEN") and k != "RESCUE_SIM_CONTROL_TOKEN"}
    env["RESCUE_MINE_DATA_PORT"] = str(port)
    proc = subprocess.Popen([sys.executable, "-m", "rescue_mine_data_sim"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(f"{base}/sim/scenarios", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@NEED_NODE
@pytest.mark.parametrize("scenario", CASES)
def test_watch_matches_expectations(sim, scenario, tmp_path):
    exp = EXPECT[scenario]["watch"]
    start = datetime.fromisoformat(_post(f"{sim}/sim/scenario", {"id": scenario, "manual": True})["now"][:19])
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESCUE_MCP_")}
    env["RESCUE_SKILL_PYTHON"] = sys.executable
    args = ["node", "tools/watch.ts", str(ROOT / "skills"), str(tmp_path / "records"), "1201", f"{sim}/mcp", f"{sim}/sim", str(exp["end_by_h"] + 1)]
    out = subprocess.run(args, cwd=BUNDLE, env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    r = got["result"]
    ended_h = (datetime.fromisoformat(r["ended_at"]) - start).total_seconds() / 3600
    assert got["ended"], f"推进 {got['advanced_h']} 小时仍未结束"
    assert r["end_reason"] == exp["end_reason"], r["summary"]
    assert ended_h <= exp["end_by_h"], (ended_h, r["summary"])
    if exp["end_reason"] == "expired":
        assert ended_h == exp["end_by_h"] and not r["anomaly"]
    reasons = "；".join(x for p in r["panels"] for x in p["reasons"])
    assert not exp.get("reasons_any") or any(w in reasons for w in exp["reasons_any"]), reasons
    if r["anomaly"]:
        assert r["report_markdown"].startswith("【风险检测】1201 工作面") and "发现异常" in r["report_markdown"]
