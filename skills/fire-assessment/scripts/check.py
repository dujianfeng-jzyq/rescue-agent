"""风险检测（design-v2 4.9）：按固定顺序计算，与完整研判用同一批函数，结论一致。

1. 数据整理：必需测点（watch.yaml 的 required）是否失效（故障、卡值、没有数据），必需测点组是否缺失；
   火区已封闭时，watch.yaml 的 sealed_exempt 里的测点组（密闭内断电）不算必需；
2. 实时报警（同 alarms.py）；
3. 预警等级（同 warning.py）；
4. 自燃阶段（同 stage.py）；
5. 数据中断：必需参数的最新读数早于 watch.yaml 的 data_gap_h（按测点组，约为几个采样周期）；
6. 与基线比较，判定是否异常（watch.yaml 的 anomaly）。

输入：{"evidence": <证据内容>, "baseline": <持续检测开始时的 check.py 输出，可省略>}。
没有基线时与“正常状态”比较：等级无、未见自燃征兆、无报警、无失效测点、无数据中断。
输出 result：等级、阶段、报警、失效测点、anomaly 与 reasons、state（下一次检测的基线）。
"""

from datetime import datetime

from _alarms import check_all
from _evidence import series
from _quality import check_series
from _skill import evidence, load_reference, main
from _warning import LEVELS, NAMES, STAGE_NAMES, STAGES, assess, classify_stage, panel_sealed

VERSION = "check/1.0.0"
NOMINAL = {"level": "none", "stage": "no_sign", "alarm_keys": [], "failed": [], "missing_groups": [], "stale": [], "nominal": True}


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


def stale_required(ev, required, max_age_h):
    """必需参数的最新读数早于 data_gap_h（按测点组）的，记为数据中断：“测点/参数（已 N 小时没有新数据）”的键。"""
    now = datetime.fromisoformat(ev["as_of"])
    out = []
    for group, measurands in required.items():
        g = ev["groups"].get(group)
        if g is None or group not in max_age_h:
            continue
        latest = {}
        for r in g["latest"]:
            if r["measurand"] in measurands:
                key = f"{r['sensor_id']}/{r['measurand']}"
                latest[key] = max(latest.get(key, r["ts"]), r["ts"])
        for key, ts in latest.items():
            if (now - datetime.fromisoformat(ts)).total_seconds() > max_age_h[group] * 3600:
                out.append(key)
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
    if cfg.get("data_gap"):
        reasons += [f"数据中断：{x} 超过规定时长没有新数据" for x in state.get("stale", []) if x not in base.get("stale", [])]
    return reasons


def escalated(state, base):
    """是否有“升级”类变化：预警等级升高、自燃阶段推进、出现新的实时报警（监听器据此立即告警；测点失效、数据中断等数据类异常不算）。"""
    return (
        LEVELS.index(state["level"]) > LEVELS.index(base["level"])
        or STAGES.index(state["stage"]) > STAGES.index(base["stage"])
        or any(k not in base["alarm_keys"] for k in state["alarm_keys"])
    )


def run(data, _args):
    ev = evidence(data)
    th = load_reference("thresholds.yaml")
    watch = load_reference("watch.yaml")
    alarm_cfg = load_reference("alarms.yaml")
    warnings: list[str] = []

    alarms = check_all(ev, alarm_cfg, warnings)
    w = assess(ev, th["levels"], th["window_h"], th["rate_window_h"])
    stage, basis = classify_stage(w["values"], th["stage"])
    exempt = sorted(set(watch.get("sealed_exempt") or []) & set(watch["required"])) if panel_sealed(ev) else []
    required = {g: m for g, m in watch["required"].items() if g not in exempt}
    state = {
        "level": w["level"],
        "stage": stage,
        "alarm_keys": sorted({f"{a['rule_id']}@{a['sensor_id']}" for a in alarms}),
        "failed": failed_required(ev, required, th["window_h"]),
        "missing_groups": sorted(m["group"] for m in ev.get("missing", []) if m["group"] not in exempt),
        "stale": stale_required(ev, required, watch["data_gap_h"]),
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
        "stale_required": state["stale"],
        "sealed": panel_sealed(ev),
        "sealed_exempt": exempt,  # 火区已封闭、不算必需的测点组
        "data_quality": w["data_quality"],
        "notes": w["notes"],
        "anomaly": bool(reasons),
        "escalated": escalated(state, base),
        "reasons": reasons,
        "compared_with": base_from,
        "baseline_state": base,
        "state": state,
    }
    params = {"anomaly": watch["anomaly"], "required": watch["required"], "sealed_exempt": watch.get("sealed_exempt") or [], "data_gap_h": watch["data_gap_h"], "status": watch["status"], "thresholds_status": th["status"]}
    if watch["status"] == "placeholder":
        warnings.append("检测判据为开发初稿（占位值），待专家评审")
    return result, params, warnings


if __name__ == "__main__":
    main(VERSION, run)
