"""工作面名变体（RESCUE_SIM_PANEL）：演示矿井的编号和名称换成与地图一致的 211016，其余不变。

PANEL 在导入时确定，所以每个检查在子进程里跑。缺省（1201）的行为由其余测试覆盖。
"""

import json
import os
import subprocess
import sys

SCRIPT = r"""
import asyncio, json
from rescue_contract.conformance import run_all
from rescue_mine_data_sim.mine import PANEL, PANELS, SENSORS
from rescue_mine_data_sim.server import SimHolder, build_server

holder = SimHolder("s4-escalation", manual_clock=True)
holder.engine.clock.advance(45)
server = build_server(holder)
sensors = {s.id: s for s in SENSORS}
print(json.dumps({
    "panel": PANEL,
    "panels": [p.panel_id for p in PANELS],
    "sensor_ids": sorted(sensors),
    "sensor_panels": sorted({s.panel for s in SENSORS if s.panel}),
    "roadways_of_uc": sensors["SM-%s-UC" % PANEL].roadway_id,
    "conformance_failures": [r for r in asyncio.run(run_all(server)) if r[1]],
}))
"""


def run(panel: str | None) -> dict:
    env = {**os.environ}
    env.pop("RESCUE_SIM_PANEL", None)
    if panel:
        env["RESCUE_SIM_PANEL"] = panel
    out = subprocess.run([sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_default_is_1201():
    r = run(None)
    assert r["panel"] == "1201" and r["panels"] == ["1201", "1202"]
    assert "SM-1201-UC" in r["sensor_ids"]


def test_variant_renames_ids_and_names_consistently():
    r = run("211016")
    assert r["panel"] == "211016" and r["panels"] == ["211016", "1202"]
    assert "SM-211016-UC" in r["sensor_ids"] and "BT-211016-G1" in r["sensor_ids"]
    assert not any("1201" in s for s in r["sensor_ids"]), "不应残留旧编号"
    assert "211016" in r["sensor_panels"] and "1201" not in r["sensor_panels"]
    assert r["roadways_of_uc"] == "R-211016-FACE"
    assert r["conformance_failures"] == [], "变体仍然满足 rescue-mine 接口一致性"
