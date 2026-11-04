"""第二版 P4：effect-review 技能与第一版 fire_model 的 evaluate_effect_panel 对同一时刻的输出一致（逐测点、总体）。

处置按 eval/agent/cases.yaml 的写法（apply）在模拟矿井上执行；第二版按 effect 清单（窗口从 since 之前 baseline_h 小时取到现在）采集。
第二版的测点按 id 排序，按（测点、参数）比较。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from rescue_contract.client import MineData
from rescue_contract.collect import collect_from, parse_collect_spec
from rescue_contract.skills import load_skill, run_script
from rescue_fire_model import effect as v1
from rescue_mine_data_sim.server import SimHolder, build_server

SKILLS = Path(__file__).resolve().parents[3] / "skills"

# （场景，处置时刻 h，处置 [(type, params, roadways)]，评估时刻 h）
MOMENTS = [
    ("s2-slow-oxidation", 48, [("inert_injection", {"rate_m3_h": 1000}, None), ("leak_sealing", {"reduction": 0.5}, ["R-1201-RET"])], 72),
    ("s2-slow-oxidation", 48, [("inert_injection", {"rate_m3_h": 1000}, None)], 54),  # 观测不足
    ("s3-accelerating", 40, [("inert_injection", {"rate_m3_h": 600}, None)], 64),
    ("s4-escalation", 60, [("inert_injection", {"rate_m3_h": 600}, None), ("temporary_seal", {}, ["R-1201-IN", "R-1201-RET"])], 84),
    ("s1-normal", 24, [], 48),
]


def _moment(scenario, at, actions, then):
    h = SimHolder(scenario, manual_clock=True)
    h.engine.clock.advance(at)
    since = h.engine.now()
    for t, prm, rw in actions:
        h.engine.apply(t, prm, roadways=rw, source="eval")
    h.engine.clock.advance(then - at)
    return h, since


@pytest.mark.parametrize(("scenario", "at", "actions", "then"), MOMENTS, ids=[f"{m[0]}@{m[1]}→{m[3]}h" for m in MOMENTS])
def test_effect_matches_v1(scenario, at, actions, then):
    h, since = _moment(scenario, at, actions, then)
    old = asyncio.run(v1.panel_effect(MineData(build_server(h)), "1201", since))
    spec = parse_collect_spec((SKILLS / "effect-review" / "references" / "collect.yaml").read_text(encoding="utf-8"))
    ev = asyncio.run(collect_from(build_server(h), spec, "effect", "1201", since.isoformat()))
    r = run_script(load_skill(SKILLS / "effect-review").scripts["effect"], {"evidence": ev})
    assert r.ok, r.detail
    new = r.output["result"]
    for k in ("since", "as_of", "observed_h", "next_review_at", "observed_h_at_next_review", "overall", "overall_name", "evaluated", "judged", "counts"):
        assert new[k] == old[k], k
    # “恶化的测点：…”里测点的顺序不同（第二版按 id 排序）
    assert [sorted(b.replace("：", "、").split("、")) for b in new["basis"]] == [sorted(b.replace("：", "、").split("、")) for b in old["basis"]]
    by_old = {(p["sensor_id"], p["measurand"]): p for p in old["points"]}
    assert sorted(by_old) == sorted((p["sensor_id"], p["measurand"]) for p in new["points"])
    for p in new["points"]:
        q = by_old[(p["sensor_id"], p["measurand"])]
        assert {k: p[k] for k in q if k in p} == {k: q[k] for k in q if k in p}, p["sensor_id"]


def test_markers_match_fire_assessment_stage():
    eff = yaml.safe_load((SKILLS / "effect-review" / "references" / "effect.yaml").read_text(encoding="utf-8"))
    th = yaml.safe_load((SKILLS / "fire-assessment" / "references" / "thresholds.yaml").read_text(encoding="utf-8"))
    assert eff["markers"] == {"C2H4": th["stage"]["accelerating"]["c2h4_ppm"], "C2H2": th["stage"]["intense"]["c2h2_ppm"]}
    spec = yaml.safe_load((SKILLS / "effect-review" / "references" / "collect.yaml").read_text(encoding="utf-8"))
    assert all(g["before_since_h"] >= eff["baseline_h"] for g in spec["kinds"]["effect"]["sensors"])
