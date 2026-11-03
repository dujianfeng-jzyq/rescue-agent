"""风险检测（design-v2 4.9）：按固定顺序计算，与完整研判用同一批函数，结论一致。

1. 数据整理：必需测点（watch.yaml 的 required）是否失效（故障、卡值、没有数据），必需测点组是否缺失；
2. 实时报警（同 alarms.py）；
3. 预警等级（同 warning.py）；
4. 自燃阶段（同 stage.py）；
5. 与基线比较，判定是否异常（watch.yaml 的 anomaly）。

输入：{"evidence": <证据内容>, "baseline": <上一次 check.py 的输出，可省略>}。
没有基线时与“正常状态”比较：等级无、未见自燃征兆、无报警、无失效测点。
输出 result：等级、阶段、报警、失效测点、anomaly 与 reasons、state（下一次检测的基线）。
"""

from _alarms import check_all
from _evidence import series
from _quality import check_series
from _skill import evidence, load_reference, main
from _warning import LEVELS, NAMES, STAGE_NAMES, STAGES, assess, classify_stage

VERSION = "check/1.0.0"
NOMINAL = {"level": "none", "stage": "no_sign", "alarm_keys": [], "failed": [], "missing_groups": [], "nominal": True}


def failed_required(ev, required, window_h):
    """必需测点组里的必需参数：整段序列被数据质量检测排除或没有数据的，记为失效。"""
    out = []
    for group, measurands in required.items():
        g = ev["groups"].get(group)
        if g is None:
            continue
        for s in g["sensors"]:
            for m in measurands:
                if m not in g["measurands"].get(s["id"], []):
                    continue
                if not check_series(f"{s['id']}/{m}", series(ev, s["id"], m, window_h)).usable:
                    out.append(f"{s['id']}/{m}")
    return sorted(out)


def compare(state, base, cfg):
    reasons = []
    if cfg.get("new_alarm"):
        new = [k for k in state["alarm_keys"] if k not in base["alarm_keys"]]
        reasons += [f"新的实时报警：{k}" for k in new]
    if cfg.get("level_up") and LEVELS.index(state["level"]) > LEVELS.index(base["level"]):
        reasons.append(f"预警等级由 {NAMES[base['level']]} 升为 {NAMES[state['level']]}")
    if cfg.get("stage_up") and STAGES.index(state["stage"]) > STAGES.index(base["stage"]):
        reasons.append(f"自燃阶段由 {STAGE_NAMES[base['stage']]} 推进为 {STAGE_NAMES[state['stage']]}")
    if cfg.get("required_failed"):
        reasons += [f"必需测点失效：{x}" for x in state["failed"] if x not in base["failed"]]
        reasons += [f"必需测点组没有匹配的测点：{x}" for x in state["missing_groups"] if x not in base["missing_groups"]]
    return reasons


def run(data, _args):
    ev = evidence(data)
    th = load_reference("thresholds.yaml")
    watch = load_reference("watch.yaml")
    alarm_cfg = load_reference("alarms.yaml")
    warnings: list[str] = []

    alarms = check_all(ev, alarm_cfg, warnings)
    w = assess(ev, th["levels"], th["window_h"], th["rate_window_h"])
    stage, basis = classify_stage(w["values"], th["stage"])
    state = {
        "level": w["level"],
        "stage": stage,
        "alarm_keys": sorted({f"{a['rule_id']}@{a['sensor_id']}" for a in alarms}),
        "failed": failed_required(ev, watch["required"], th["window_h"]),
        "missing_groups": sorted(m["group"] for m in ev.get("missing", [])),
        "nominal": False,
    }
    baseline = data.get("baseline")
    if baseline is None:
        base, base_from = NOMINAL, "正常状态（没有基线）"
    else:
        if not isinstance(baseline, dict) or not isinstance(baseline.get("result", {}).get("state"), dict):
            raise ValueError("baseline 应为上一次 check.py 的输出")
        if baseline["result"].get("panel") != ev["panel"]:
            raise ValueError(f"基线是工作面 {baseline['result'].get('panel')} 的，与证据的 {ev['panel']} 不符")
        base, base_from = baseline["result"]["state"], f"基线检测（矿井时间 {baseline['result']['as_of']}）"
    reasons = compare(state, base, watch["anomaly"])
    result = {
        "panel": ev["panel"],
        "as_of": ev["as_of"],
        "level": w["level"],
        "level_name": w["level_name"],
        "triggered": w["triggered"],
        "stage": stage,
        "stage_name": STAGE_NAMES[stage],
        "stage_basis": basis,
        "alarms": alarms,
        "alarm_count": len(alarms),
        "failed_required": state["failed"],
        "missing_groups": state["missing_groups"],
        "data_quality": w["data_quality"],
        "notes": w["notes"],
        "anomaly": bool(reasons),
        "reasons": reasons,
        "compared_with": base_from,
        "baseline_state": base,
        "state": state,
    }
    params = {"anomaly": watch["anomaly"], "required": watch["required"], "status": watch["status"], "thresholds_status": th["status"]}
    if watch["status"] == "placeholder":
        warnings.append("检测判据为开发初稿（占位值），待专家评审")
    return result, params, warnings


if __name__ == "__main__":
    main(VERSION, run)
