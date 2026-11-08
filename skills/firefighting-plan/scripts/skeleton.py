"""防灭火处置方案骨架：现场条件、必备步骤、可选步骤的选择清单。

与封闭方案（sealing-plan 的 skeleton.py）同一套现场条件的取法，区别是：不安排人员进入灾区、不封闭火区，
所以没有撤人和密闭巷道的选择，也没有需要人工确认的现场条件（灾区温度、能见度、火源是否明确等只有进入灾区的步骤才需要）。
- 甲烷、是否上升：本工作面巷道安全监控测点（非束管）的 CH4（references/plan.yaml 的窗口和速率）；
- 氧浓度、爆炸危险性：explosion-triangle 的结果（多测点取最不利，氧浓度取最不利测点的 O2）。

输入：{"evidence": <firefighting-plan 的 plan 证据>, "explosion": <explosion_triangle 的输出>,
       "options": {"leak_m3_min"?: 数值, "target_o2_pct"?: 数值}}。
脚本不选可选步骤：由“方案编写”阶段从这里给出的清单里选，再由 compose.py 组装。
"""

from _evidence import series
from _quality import check_series, slope_per_hour
from _skill import evidence, load_reference, main, record_result

VERSION = "skeleton/1.0.0"


def methane(ev, panel, cfg):
    """（最大最新值, 是否上升, 数据质量说明）。"""
    g = ev["groups"].get("methane")
    sensors = [s for s in (g["sensors"] if g else []) if panel in s["id"] and s.get("source") != "bundle_tube" and "CH4" in s.get("measurands", [])]
    if not sensors:
        return None, None, [f"工作面 {panel} 没有巷道甲烷测点，甲烷浓度缺失"]
    latest, rates, notes = [], [], []
    for s in sensors:
        rep = check_series(f"{s['id']}/CH4", series(ev, s["id"], "CH4", cfg["methane_window_h"]))
        notes += rep.flags
        if rep.usable:
            latest.append(rep.usable[-1]["value"])
            if (rate := slope_per_hour(rep.usable)) is not None:
                rates.append(rate)
    pct = round(max(latest), 3) if latest else None
    rising = None if not rates else any(r > cfg["methane_rising_rate_pct_h"] for r in rates)
    return pct, rising, notes


def run(data, _args):
    ev = evidence(data)
    xr = record_result(data, "explosion")
    cfg = load_reference("plan.yaml")
    opts = data.get("options") or {}
    panel = ev["panel"]
    if "network" not in ev:
        raise ValueError("证据里没有通风网络（采集清单 plan 应取 network）")
    leak, target = opts.get("leak_m3_min"), opts.get("target_o2_pct")
    for name, v in (("leak_m3_min", leak), ("target_o2_pct", target)):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int | float) or v <= 0):
            raise ValueError(f"{name} 应为正数")

    pct, rising, notes = methane(ev, panel, cfg)
    worst = next((p for p in xr.get("points", []) if p["sensor_id"] == xr.get("worst_sensor_id")), None)
    o2 = worst["sample"]["readings"].get("O2") if worst else None
    context = {
        "fire_type": "goaf_spontaneous_combustion",
        "as_of": ev["as_of"],
        "methane_pct": pct,
        "methane_rising": rising,
        "o2_pct": o2,
        "area_temperature_c": None,
        "visibility_m": None,
        "explosion_risk": xr.get("overall", "unknown"),
        "fire_source_confirmed": None,
        "water_supply_sufficient": None,
        "return_airway_clear": None,
        "sealed_at": None,
    }

    roads = [r for r in ev["network"]["roadways"] if panel in r["id"]]
    leak_roadways = [r["id"] for r in roads if r["kind"] == cfg["leak_sealing_roadway_kind"]]

    missing = []
    if leak is None or target is None:
        missing.append({"field": "inert_rate", "label": "注氮流量", "value_type": "number", "kind": "calculation", "note": "给出漏风量 leak_m3_min 和目标氧浓度 target_o2_pct 后由代码按注氮稀释公式估算"})
    elif o2 is None:
        missing.append({"field": "inert_rate", "label": "注氮流量", "value_type": "number", "kind": "calculation", "note": "没有可用的束管氧浓度，不能估算注氮流量"})

    def steps(items):
        return [{k: s[k] for k in ("key", "type", "description", "roadways", "after", "label") if k in s} for s in items]

    warnings = list(notes)
    if xr.get("overall") == "unknown":
        warnings.append("爆炸危险性无法判定，处置期间按有爆炸危险对待")
    result = {
        "panel": panel,
        "as_of": ev["as_of"],
        "kind": cfg["kind"],
        "title": f"{panel} {cfg['title']}",
        "plan_id": f"{cfg['plan_id_prefix']}-{panel}-{ev['as_of'][:16].replace('-', '').replace('T', '').replace(':', '')}",
        "context": context,
        "context_sources": {
            "methane_pct": f"工作面 {panel} 巷道安全监控测点最近 {cfg['methane_window_h']} 小时最新可用 CH4 的最大值",
            "methane_rising": f"任一测点速率 > {cfg['methane_rising_rate_pct_h']} %/h 为是；算不出速率为缺失",
            "o2_pct": f"爆炸危险性最不利测点 {xr['worst_sensor_id']} 的 O2" if worst else "无可用束管测点，缺失",
            "explosion_risk": "爆炸三角形（多测点取最不利）",
        },
        "explosion": {"overall": xr.get("overall"), "overall_name": xr.get("overall_name"), "worst_sensor_id": xr.get("worst_sensor_id")},
        "human_inputs": [],
        "inert_inputs": {"leak_m3_min": leak, "target_o2_pct": target},
        "missing_inputs": missing,
        "required_steps": steps(cfg["required_steps"]),
        "optional_steps": steps(cfg["optional_steps"]),
        "seal_roadway_options": [],
        "evacuate_roadways": [],
        "leak_roadways": leak_roadways,
        "basis": [{"document": cfg["basis"]["document"], "article": a} for a in cfg["basis"]["articles"]],
        "limits": cfg["limits"],
        "data_quality": notes,
    }
    params = {k: cfg[k] for k in ("methane_window_h", "methane_rising_rate_pct_h", "leak_sealing_roadway_kind")}
    return result, params, warnings


if __name__ == "__main__":
    main(VERSION, run)
