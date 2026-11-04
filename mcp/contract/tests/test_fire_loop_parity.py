"""固定流程 run_fire_assessment 与风险检测（TS，dsh-bundle/tools/fire-assessment.ts）与 Python 侧逐项一致。

起一个手动时钟的模拟矿井（s4-escalation 第 60 小时：红色预警、加速氧化、建议升级、计算受威胁人员），TS 侧跑一次流程，然后：
- 每条记录能被 Python 的 RecordStore 读出并通过 content_sha256 核对（actions 以后就这样核对）；
- TS 采到的证据与 Python 采集小工具相同；
- 每个结果记录：用记录里登记的输入在 Python 侧重新运行同一脚本，结果相同（runScript 组织输入的方式与 skills 运行器一致）；
- 每个报告记录：Python 按同样的数据渲染同一模板，结果相同。

没有 node 或 dsh-bundle 依赖时跳过。
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from rescue_contract.collect import collect_from, parse_collect_spec
from rescue_contract.records import RecordStore
from rescue_contract.skills import load_skill, run_script
from rescue_contract.template import render, report_data

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "dsh-bundle"
SKILLS = ROOT / "skills"
NEED_NODE = pytest.mark.skipif(shutil.which("node") is None or not (BUNDLE / "node_modules" / "yaml").exists(), reason="没有 node 或 dsh-bundle 依赖")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


@pytest.fixture(scope="module")
def sim_s4_60():
    import time

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
        _post(f"{base}/sim/advance", {"hours": 60})
        yield f"{base}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@NEED_NODE
def test_ts_flow_matches_python(sim_s4_60, tmp_path, monkeypatch):
    monkeypatch.delenv("RESCUE_MCP_TOKEN", raising=False)
    rec_dir, ws = tmp_path / "records", tmp_path / "workspace"
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESCUE_MCP_")}
    args = ["node", "tools/fire-assessment.ts", str(SKILLS), str(rec_dir), str(ws), sys.executable, "1201", sim_s4_60]
    out = subprocess.run(args, cwd=BUNDLE, env=env, capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    a, records = got["assessment"], got["records"]

    # 流程结果：这个时刻应有的结论（与第一版一致的部分由 workflow/tests/test_skill_parity.py 保证）
    assert a["result"]["warning"]["level"] == "red"
    assert a["result"]["escalation"]["recommend"] is True
    assert a["result"]["personnel"]["computed"] is True
    assert [e["status"] for e in a["events"]].count("failed") == 0
    # 命令行工具没有阶段子 Agent：研判说明跳过，报告注明未生成
    assert a["result"]["explanation"]["generated"] is False
    assert "【研判说明】研判说明未生成" in a["report_markdown"]
    assert a["result"]["explosion"]["chart_markdown"] and Path(a["result"]["explosion"]["chart_markdown"].split("](")[1][:-1]).is_file()
    assert a["report_markdown"].startswith("【预警研判】1201 工作面")
    assert "【升级建议】1201 工作面" in a["report_markdown"]
    assert got["check"]["anomaly"] is True

    # 记录都能被 Python 读出并核对哈希
    store = RecordStore(rec_dir)
    for rid in records:
        store.get(rid)

    # 证据与 Python 采集小工具相同（同一时刻、同一清单）
    spec = parse_collect_spec((SKILLS / "fire-assessment" / "references" / "collect.yaml").read_text(encoding="utf-8"))
    ev_id = a["record_ids"]["evidence"]
    assert records[ev_id]["content"] == asyncio.run(collect_from(sim_s4_60, spec, "assessment", "1201"))

    # 结果记录：按登记的输入在 Python 侧重跑同一脚本
    n_results = n_reports = 0
    for rid, rec in records.items():
        src = rec["source"]
        if rec["kind"] == "result":
            data = {name: records[i]["content"] for name, i in src["inputs"].items()}
            if src["options"]:
                data["options"] = src["options"]
            r = run_script(load_skill(SKILLS / src["skill"]).scripts[src["script"]], data)
            assert r.ok, r.detail
            assert r.output == rec["content"], f"{rid}（{src['skill']}/{src['script']}）与 Python 运行结果不同"
            n_results += 1
        elif rec["kind"] == "report":
            tpl = (SKILLS / src["skill"] / src["template"]).read_text(encoding="utf-8")
            data = report_data({name: records[i] for name, i in src["inputs"].items()}, src["extra"])
            assert render(tpl, data) == rec["content"], f"{rid}（{src['skill']}/{src['template']}）与 Python 渲染结果不同"
            n_reports += 1
    assert n_results == 9  # 研判 8 个脚本（alarms、warning、stage、trend、explosion_triangle、personnel、escalation、explanation_options）+ 检测 1 个
    assert n_reports == 3  # 研判报告、升级建议、检测结果
