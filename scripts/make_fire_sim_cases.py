#!/usr/bin/env python3
"""生成 fire-assessment 的 fire_sim_request、fire_sim_compare 自检样例（第七版，docs/design-v7-fire-sim.md 附录 A.5）。

    uv run --project mcp python scripts/make_fire_sim_cases.py [提供方地址，缺省 http://192.168.0.200:5079]

在仓库根目录运行。输入取现有样例（route_request 的 s4 红色证据和 personnel、warning 的 s4 红色和 s5 橙色）；
s4 红色那组**会调用提供方服务**（经 rescue_fire_sim 适配层），所以不并进 make_skill_cases.py。
personnel 或 warning 样例重新生成后要重跑本脚本；提供方结果变了时 s4 两组的期望会跟着变，提交前看 diff。
"""

import asyncio
import copy
import json
import sys
from pathlib import Path

from rescue_contract.skills import load_skill, run_script
from rescue_fire_sim.adapter import SimInput
from rescue_fire_sim.service import Config, simulate

ROOT = Path.cwd()
FA = ROOT / "skills/fire-assessment"
CASES = FA / "tests/cases"
skill = load_skill(FA)


def run(name, data):
    r = run_script(skill.scripts[name], data)
    if not r.ok:
        sys.exit(f"{name}: {r.status} {r.detail}")
    return r.output


def write(script, case, data, out):
    d = CASES / script
    d.mkdir(exist_ok=True)
    (d / f"{case}.input.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (d / f"{case}.expected.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def load(p):
    return json.loads((CASES / p).read_text(encoding="utf-8"))


red_ev = load("route_request/s4-escalation-60h-red.input.json")["evidence"]
personnel = load("route_request/s4-escalation-60h-red.input.json")["personnel"]
red_w = load("warning/s4-escalation-60h-red.expected.json")
orange_in = load("warning/s5-sealed-42h-released-orange.input.json")
orange_w = load("warning/s5-sealed-42h-released-orange.expected.json")

req_in = {"evidence": red_ev, "warning": red_w}
req = run("fire_sim_request", req_in)
write("fire_sim_request", "s4-escalation-60h-red", req_in, req)
nreq_in = {"evidence": orange_in["evidence"], "warning": orange_w}
nreq = run("fire_sim_request", nreq_in)
write("fire_sim_request", "s5-sealed-42h-orange-not-needed", nreq_in, nreq)


def sim_ev(result=None, error=None):
    r = req["result"]
    return {
        "kind": "fire_sim",
        "panel": r["panel"],
        "as_of": r["as_of"],
        "request_record_id": "REC-test",
        "result": result,
        "error": error,
    }


URL = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.0.200:5079"
real = asyncio.run(simulate(SimInput(**req["result"]["request"]), Config(url=URL)))
assert real["available"], real["error"]
base = {"evidence": red_ev, "personnel": personnel, "fire_sim_request": req}
for case, ev in (
    ("s4-escalation-60h-red-service", sim_ev(real)),
    (
        "s4-provider-500",
        sim_ev(
            {
                **real,
                "available": False,
                "error": "服务返回 500：火风压计算失败: peakMassBurningRate must be finite and greater than zero",
                "times_s": [],
                "branches": [],
            }
        ),
    ),
    ("s4-plugin-error", sim_ev(None, "fire_sim 调用失败：连接被拒绝")),
):
    data = {**base, "fire_sim": ev}
    write("fire_sim_compare", case, data, run("fire_sim_compare", data))
nd = {
    "evidence": orange_in["evidence"],
    "personnel": personnel,
    "fire_sim_request": nreq,
}
write("fire_sim_compare", "s5-not-needed", nd, run("fire_sim_compare", nd))
out = json.loads((CASES / "fire_sim_compare/s4-escalation-60h-red-service.expected.json").read_text())
print("\n".join(out["result"]["lines"]))
print(out["result"]["counts"])

# 人为改写的推演结果：CO 沿 1201 运输巷倒灌到避难硐室联络巷、主运输大巷（覆盖 extra_people、避灾路线危险、风流反转三条分支）
syn = copy.deepcopy(real)
n = len(syn["times_s"])


def ramp(start, top):
    return [0.0 if k < start else float(min(top, (k - start + 1) * 50)) for k in range(n)]


for b in syn["branches"]:
    if b["id"] in ("R-1201-IN#b", "R-REFUGE#a"):
        b["co_max_ppm"] = ramp(5, 600)
        b["co_first_s"] = syn["times_s"][5]
        b["co_peak_ppm"] = 600.0
    if b["id"] == "R-MAIN-IN#b":
        b["co_max_ppm"] = ramp(20, 100)
        b["co_first_s"] = syn["times_s"][20]
        b["co_peak_ppm"] = 100.0
    if b["id"] == "R-1201-IN#b":
        b["air_speed_m_s"] = [v if k < 10 else -0.3 for k, v in enumerate(b["air_speed_m_s"])]
        b["air_reversed"] = True
data = {**base, "fire_sim": sim_ev(syn)}
write("fire_sim_compare", "s4-synthetic-backflow", data, run("fire_sim_compare", data))
print("\n".join(json.loads((CASES / "fire_sim_compare/s4-synthetic-backflow.expected.json").read_text())["result"]["lines"]))
