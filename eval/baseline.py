#!/usr/bin/env python3
"""确定性基线评测：不经过大模型，直接用 fire-model 的计算逐小时扫描各标准场景。

目的：
1. 给“模型层”（预警分级、数据质量、复燃报警）一个可回归的基线——改阈值、改模拟参数、改算法后跑一遍就知道变好还是变坏；
2. 以后评测智能体时，把它的结论和这里的基线对照，区分“模型算错”和“Agent 用错”。

    uv run --project mcp python eval/baseline.py            # 全部场景，打印表格，写 var/eval/baseline.json
    uv run --project mcp python eval/baseline.py s2 s6      # 指定场景（前缀匹配）

期望值在 eval/expectations.yaml，目前是开发占位，需要专家按实际阈值审定。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import yaml
from rescue_contract.client import MineData
from rescue_fire_model import alarms, warning
from rescue_mine_data_sim.scenarios import load_all
from rescue_mine_data_sim.server import SimHolder, build_server

ROOT = Path(__file__).resolve().parents[1]
EXPECT = yaml.safe_load((ROOT / "eval" / "expectations.yaml").read_text(encoding="utf-8"))
LEVELS = ["none", "blue", "yellow", "orange", "red"]


async def scan(scenario_id: str, step_h: float, horizon_h: float, apply: list[dict] | None = None, check_reignition: bool = False) -> dict:
    holder = SimHolder(scenario_id, manual_clock=True)
    pending = sorted(apply or [], key=lambda a: a["at_h"])
    md = MineData(build_server(holder))
    timeline, first_at, reignition_at = [], {}, None
    t = 0.0
    while t <= horizon_h + 1e-9:
        if t:
            holder.engine.clock.advance(step_h)
        while pending and pending[0]["at_h"] <= t:
            act = pending.pop(0)
            holder.engine.apply(act["type"], act.get("params"), source="eval")
        a = await warning.assess(md)
        lvl = a.level.value
        first_at.setdefault(lvl, t)
        if check_reignition and reignition_at is None and await alarms.check_reignition(md):
            reignition_at = t
        timeline.append({"h": t, "level": lvl, "truth_c": holder.engine.hotspot()["temperature_c"]})
        t += step_h
    return {"scenario": scenario_id, "first_at_h": first_at, "max_level": max((p["level"] for p in timeline), key=LEVELS.index),
            "reignition_alarm_at_h": reignition_at, "timeline": timeline}


def judge(result: dict, exp: dict) -> list[str]:
    """返回不满足期望的说明；空列表表示通过。"""
    fails = []
    if "max_level" in exp and LEVELS.index(result["max_level"]) > LEVELS.index(exp["max_level"]):
        fails.append(f"最高等级 {result['max_level']} 超过期望上限 {exp['max_level']}（误报）")
    for lvl, deadline in (exp.get("reach_by_h") or {}).items():
        reached = [h for level, h in result["first_at_h"].items() if LEVELS.index(level) >= LEVELS.index(lvl)]
        if not reached or min(reached) > deadline:
            fails.append(f"未在 {deadline}h 内达到 {lvl}（实际 {min(reached) if reached else '未达到'}）")
    if exp.get("reignition_alarm") is True and result["reignition_alarm_at_h"] is None:
        fails.append("未触发复燃报警")
    if exp.get("reignition_alarm") is False and result["reignition_alarm_at_h"] is not None:
        fails.append(f"误触发复燃报警（{result['reignition_alarm_at_h']}h）")
    nb = exp.get("reignition_not_before_h")
    if nb is not None and result["reignition_alarm_at_h"] is not None and result["reignition_alarm_at_h"] < nb:
        fails.append(f"复燃报警过早（{result['reignition_alarm_at_h']}h，封闭期间不应报警）")
    return fails


async def main(prefixes: list[str]) -> int:
    ids = [s for s in sorted(load_all()) if not prefixes or any(s.startswith(p) for p in prefixes)]
    results, failed = [], 0
    print(f"{'场景':<22}{'最高':<8}{'首次到达(h)':<44}{'复燃报警':<10}结果")
    for sid in ids:
        exp = EXPECT.get(sid, {})
        r = await scan(sid, exp.get("step_h", 3), exp.get("horizon_h", 72), exp.get("apply"), "reignition_alarm" in exp)
        r["failures"] = judge(r, exp)
        failed += bool(r["failures"])
        firsts = " ".join(f"{k}={v:g}" for k, v in sorted(r["first_at_h"].items(), key=lambda kv: LEVELS.index(kv[0])))
        rg = "—" if r["reignition_alarm_at_h"] is None else f"{r['reignition_alarm_at_h']:g}h"
        print(f"{sid:<22}{r['max_level']:<8}{firsts:<44}{rg:<10}{'通过' if not r['failures'] else '不通过：' + '；'.join(r['failures'])}")
        results.append(r)
    out = ROOT / "var" / "eval" / "baseline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(ids) - failed}/{len(ids)} 通过；明细：{out.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
