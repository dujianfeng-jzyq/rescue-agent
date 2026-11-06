"""封闭方案骨架：现场条件、必备步骤、可选步骤和密闭巷道的选择清单。

由第一版 mcp/workflow 的 plan.py（draft，kind=sealing）迁移，现场条件的取法和必备步骤不变：
- 甲烷、是否上升：本工作面巷道安全监控测点（非束管）的 CH4（references/plan.yaml 的窗口和速率）；
- 氧浓度、爆炸危险性：explosion-triangle 的结果（多测点取最不利，氧浓度取最不利测点的 O2）；
- 封闭时间：火区台账里本工作面已封闭火区的封闭时间；
- 灾区温度、能见度、火源是否明确、水源是否充足、回风道是否畅通：只来自人工输入（写明确认人），缺了就列为缺失。

输入：{"evidence": <sealing-plan 的 plan 证据>, "explosion": <explosion_triangle 的输出>, "personnel": <personnel 的输出>,
       "options": {"human_inputs": [{field, value, confirmed_by, note?}], "leak_m3_min"?: 数值, "target_o2_pct"?: 数值}}。
脚本不选可选步骤、不选密闭巷道：这些由“方案编写”阶段从这里给出的清单里选，再由 compose.py 组装。
"""

from _evidence import series
from _quality import check_series, slope_per_hour
from _skill import evidence, load_reference, main, record_result

VERSION = "skeleton/1.0.0"

# 人工确认项（方案现场条件字段）：中文名、取值类型
HUMAN_FIELDS = {
    "area_temperature_c": ("灾区温度（℃）", "number"),
    "visibility_m": ("能见度（米）", "number"),
    "fire_source_confirmed": ("火源是否明确", "bool"),
    "water_supply_sufficient": ("水源、人力和物力是否充足", "bool"),
    "return_airway_clear": ("回风道是否畅通", "bool"),
}


def human_inputs(raw):
    out, seen = [], set()
    for h in raw or []:
        f = h.get("field")
        if f not in HUMAN_FIELDS:
            raise ValueError(f"human_inputs 的 field 只能是 {'、'.join(HUMAN_FIELDS)}，收到 {f!r}")
        if f in seen:
            raise ValueError(f"human_inputs 里 {f} 重复")
        seen.add(f)
        v = h.get("value")
        if HUMAN_FIELDS[f][1] == "number" and (isinstance(v, bool) or not isinstance(v, int | float)):
            raise ValueError(f"{f} 应为数值")
        if HUMAN_FIELDS[f][1] == "bool" and not isinstance(v, bool):
            raise ValueError(f"{f} 应为 true 或 false")
        if not str(h.get("confirmed_by") or "").strip():
            raise ValueError(f"{f} 缺少确认人 confirmed_by")
        out.append({"field": f, "label": HUMAN_FIELDS[f][0], "value": float(v) if HUMAN_FIELDS[f][1] == "number" else v, "confirmed_by": str(h["confirmed_by"]).strip(), "note": h.get("note")})
    return out


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


def sealed_at(ev, panel):
    times = [a["sealed_at"] for a in ev.get("fire_areas") or [] if a["panel"] == panel and a["status"] == "sealed"]
    return max(times) if times else None


def run(data, _args):
    ev = evidence(data)
    xr = record_result(data, "explosion")
    pe = record_result(data, "personnel")
    cfg = load_reference("plan.yaml")
    opts = data.get("options") or {}
    panel = ev["panel"]
    if "network" not in ev:
        raise ValueError("证据里没有通风网络（采集清单 plan 应取 network）")
    humans = human_inputs(opts.get("human_inputs"))
    leak, target = opts.get("leak_m3_min"), opts.get("target_o2_pct")
    for name, v in (("leak_m3_min", leak), ("target_o2_pct", target)):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int | float) or v <= 0):
            raise ValueError(f"{name} 应为正数")

    pct, rising, notes = methane(ev, panel, cfg)
    worst = next((p for p in xr.get("points", []) if p["sensor_id"] == xr.get("worst_sensor_id")), None)
    o2 = worst["sample"]["readings"].get("O2") if worst else None
    given = {h["field"]: h["value"] for h in humans}
    context = {
        "fire_type": "goaf_spontaneous_combustion",
        "as_of": ev["as_of"],
        "methane_pct": pct,
        "methane_rising": rising,
        "o2_pct": o2,
        "area_temperature_c": given.get("area_temperature_c"),
        "visibility_m": given.get("visibility_m"),
        "explosion_risk": xr.get("overall", "unknown"),
        "fire_source_confirmed": given.get("fire_source_confirmed"),
        "water_supply_sufficient": given.get("water_supply_sufficient"),
        "return_airway_clear": given.get("return_airway_clear"),
        "sealed_at": sealed_at(ev, panel),
    }

    roads = [r for r in ev["network"]["roadways"] if panel in r["id"]]
    seal_options = [{"roadway_id": r["id"], "name": r["name"], "kind": r["kind"]} for r in roads if r["kind"] in cfg["seal_roadway_kinds"]]
    leak_roadways = [r["id"] for r in roads if r["kind"] == cfg["leak_sealing_roadway_kind"]]
    threatened = [g["roadway_id"] for g in pe.get("threatened", [])] if pe.get("computed") else []

    missing = [
        {"field": f, "label": label, "value_type": vt, "kind": "human", "note": "系统没有数据，需现场确认后作为人工输入（写明确认人）重新编制"}
        for f, (label, vt) in HUMAN_FIELDS.items()
        if f not in given
    ]
    if not threatened:
        missing.append({"field": "evacuate_roadways", "label": "撤人巷道", "value_type": "text", "kind": "note", "note": "未算出受威胁人员所在巷道，撤人范围按预案确定"})
    if leak is None or target is None:
        missing.append({"field": "inert_rate", "label": "注氮流量", "value_type": "number", "kind": "calculation", "note": "给出漏风量 leak_m3_min 和目标氧浓度 target_o2_pct 后由代码按注氮稀释公式估算"})
    elif o2 is None:
        missing.append({"field": "inert_rate", "label": "注氮流量", "value_type": "number", "kind": "calculation", "note": "没有可用的束管氧浓度，不能估算注氮流量"})

    def steps(items):
        return [{k: s[k] for k in ("key", "type", "description", "roadways", "after", "label") if k in s} for s in items]

    warnings = list(notes)
    if xr.get("overall") == "unknown":
        warnings.append("爆炸危险性无法判定，按有爆炸危险对待（第85条：封闭前先注惰抑爆）")
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
            "sealed_at": "火区台账中本工作面已封闭火区的封闭时间",
            "human": "只来自人工输入（写明确认人）",
        },
        "explosion": {"overall": xr.get("overall"), "overall_name": xr.get("overall_name"), "worst_sensor_id": xr.get("worst_sensor_id")},
        "human_inputs": humans,
        "inert_inputs": {"leak_m3_min": leak, "target_o2_pct": target},
        "missing_inputs": missing,
        "required_steps": steps(cfg["required_steps"]),
        "optional_steps": steps(cfg["optional_steps"]),
        "seal_roadway_options": seal_options,
        "evacuate_roadways": threatened,
        "leak_roadways": leak_roadways,
        "basis": [{"document": cfg["basis"]["document"], "article": a} for a in cfg["basis"]["articles"]],
        "limits": cfg["limits"],
        "data_quality": notes,
    }
    params = {k: cfg[k] for k in ("methane_window_h", "methane_rising_rate_pct_h", "seal_roadway_kinds", "leak_sealing_roadway_kind")}
    return result, params, warnings


if __name__ == "__main__":
    main(VERSION, run)
