"""启封条件判断与启封方案（第三版遗留项：启封审批按记录重做）。

输入：{"evidence": <unsealing-plan 的 unseal 证据>}。判定逐项给出（每项：取值、限值、是否满足、说明），全部满足才算具备启封条件：
1. 封闭时长：火区台账里本工作面已封闭火区的封闭时间到证据时刻 ≥ min_sealed_h；
2. 氧浓度：本工作面全部束管测点最新可用 O2 的最大值 ≤ max_o2_pct；
3. CO：最大值 ≤ max_co_ppm，且窗口内最大上升速率 ≤ max_co_rise_ppm_h；
4. 乙烯：最新读数（质量正常的）最大值 ≤ max_c2h4_ppm（不得检出；不检出时读数恒为 0，数据质量检测会当作卡值，所以不经它）；
5. 温度：采空区测温最新可用值的最大值 ≤ max_temp_c。
数据缺失、被数据质量检测排除、最新可用读数过旧（超过 max_stale_h）的，只要有任何一个测点如此，对应项就判“无法判定”，
按不满足处理（fail closed）——不能因为最不利的测点失效，就拿剩下的测点判满足；CO 上升速率样本不足也是无法判定。整体不满足时不生成方案。
具备条件时按 references/readiness.yaml 的固定步骤生成启封方案（类型 unsealing，步骤顺序满足红线 RL-164），
方案现场条件里只填系统有数据的字段，其余留空（红线规则对未知值按不放行处理）。
所有数值、判定和步骤数都在这里算好，照抄，不要自己数或比较。判据全是开发占位值。
"""

from datetime import datetime, timedelta

from _evidence import series
from _quality import check_series, slope_per_hour, ts
from _skill import evidence, load_reference, main

VERSION = "unsealing_plan/1.0.0"


def _latest(ev, window_h, measurand, notes, group, max_stale_h):
    """本组各测点最新可用值：({sensor_id: value}, {sensor_id: 窗口序列斜率}, 无法判定的测点 id 列表)。
    无可用读数、最新可用读数比证据时刻早 max_stale_h 以上的测点，都进“无法判定”。"""
    out, slopes, missing = {}, {}, []
    g = ev["groups"].get(group) or {"sensors": [], "measurands": {}}
    now = datetime.fromisoformat(ev["as_of"])
    for s in g["sensors"]:
        if measurand not in g["measurands"].get(s["id"], []):
            continue
        rep = check_series(f"{s['id']}/{measurand}", series(ev, s["id"], measurand, window_h))
        notes += rep.flags
        if not rep.usable or now - ts(rep.usable[-1]) > timedelta(hours=max_stale_h):
            missing.append(s["id"])
            continue
        out[s["id"]] = rep.usable[-1]["value"]
        slope = slope_per_hour(rep.usable)
        if slope is not None:
            slopes[s["id"]] = slope
    return out, slopes, missing


def _max_row(key, label, unit, vals, limit, what, missing=()):
    if not vals or missing:
        why = f"测点 {'、'.join(missing)} 没有可用的最新数据" if missing else "没有可用数据"
        return {"key": key, "label": label, "value": None, "limit": limit, "ok": False, "judged": False, "text": f"{label}：无法判定（{why}）；限值 ≤ {limit} {unit}", "sensor_id": None}
    sid = max(vals, key=vals.get)
    v = round(vals[sid], 3)
    ok = v <= limit
    return {"key": key, "label": label, "value": v, "limit": limit, "ok": ok, "judged": True, "sensor_id": sid, "text": f"{label}：{what}{v} {unit}（{sid}），限值 ≤ {limit} {unit}，{'满足' if ok else '不满足'}"}


def run(data, _args):
    ev = evidence(data)
    cfg = load_reference("readiness.yaml")
    panel, now = ev["panel"], datetime.fromisoformat(ev["as_of"])
    notes: list[str] = []
    w = cfg["window_h"]

    times = [a["sealed_at"] for a in ev.get("fire_areas") or [] if a["panel"] == panel and a["status"] == "sealed"]
    sealed_at = max(times) if times else None
    if sealed_at is None:
        rows = [{"key": "sealed_h", "label": "封闭时长", "value": None, "limit": cfg["min_sealed_h"], "ok": False, "judged": False, "sensor_id": None,
                 "text": f"封闭时长：无法判定（火区台账里没有工作面 {panel} 的已封闭火区）；限值 ≥ {cfg['min_sealed_h']} 小时"}]  # fmt: skip
    else:
        h = round((now - datetime.fromisoformat(sealed_at)).total_seconds() / 3600, 1)
        ok = h >= cfg["min_sealed_h"]
        rows = [{"key": "sealed_h", "label": "封闭时长", "value": h, "limit": cfg["min_sealed_h"], "ok": ok, "judged": True, "sensor_id": None,
                 "text": f"封闭时长：{h} 小时（封闭于 {sealed_at[:19]}），限值 ≥ {cfg['min_sealed_h']} 小时，{'满足' if ok else '不满足'}"}]  # fmt: skip

    stale = cfg["max_stale_h"]
    o2, _, o2_missing = _latest(ev, w, "O2", notes, "tubes", stale)
    co, co_slopes, co_missing = _latest(ev, w, "CO", notes, "tubes", stale)
    # 乙烯：不检出时读数恒为 0，数据质量检测会把整段恒值当“卡值”排除，所以取最新读数本身（质量正常的）；
    # 没有最新读数、或最新读数质量不正常的测点，整项无法判定
    tube = ev["groups"].get("tubes") or {"sensors": [], "measurands": {}, "latest": []}
    c2h4, c2h4_missing = {}, []
    for sn in tube["sensors"]:
        if "C2H4" not in tube["measurands"].get(sn["id"], []):
            continue
        r = next((x for x in tube["latest"] if x["sensor_id"] == sn["id"] and x["measurand"] == "C2H4"), None)
        if r and r.get("quality") == "ok" and r.get("value") is not None and datetime.fromisoformat(ev["as_of"]) - datetime.fromisoformat(r["ts"]) <= timedelta(hours=stale):
            c2h4[sn["id"]] = r["value"]
        else:
            c2h4_missing.append(sn["id"])
    rows.append(_max_row("o2", "氧浓度", "%", o2, cfg["max_o2_pct"], "最大 ", o2_missing))
    rows.append(_max_row("co", "CO 浓度", "ppm", co, cfg["max_co_ppm"], "最大 ", co_missing))
    co_rise_missing = co_missing + [k for k in co if k not in co_slopes]  # 样本不足 3 点算不出斜率
    rows.append(_max_row("co_rise", "CO 上升速率", "ppm/h", {k: round(v, 4) for k, v in co_slopes.items()}, cfg["max_co_rise_ppm_h"], "最大 ", co_rise_missing))
    rows.append(_max_row("c2h4", "乙烯", "ppm", c2h4, cfg["max_c2h4_ppm"], "最大 ", c2h4_missing))
    temps, _, temp_missing = _latest(ev, w, "TEMP", notes, "goaf_temperature", stale)
    rows.append(_max_row("temp", "采空区温度", "℃", temps, cfg["max_temp_c"], "最高 ", temp_missing))

    unmet = [r for r in rows if not r["ok"]]
    ready = not unmet
    plan_cfg = cfg["plan"]
    plan, steps_view = None, []
    if ready:
        roads = [r["id"] for r in ev["network"]["roadways"] if panel in r["id"] and r["kind"] == plan_cfg["return_roadway_kind"]]
        steps = []
        for i, s in enumerate(plan_cfg["steps"], start=1):
            rw = list(roads) if s.get("roadways") == "return" else []
            steps.append({"order": i, "type": s["type"], "description": s["description"], "roadways": rw, "team_size": None, "standby_team": None, "params": {}})
            steps_view.append({"order": i, "key": s["key"], "type": s["type"], "description": s["description"], "roadways_text": "、".join(rw)})
        plan = {
            "plan_id": f"{plan_cfg['plan_id_prefix']}-{panel}-{ev['as_of'][:16].replace('-', '').replace('T', '').replace(':', '')}",
            "kind": plan_cfg["kind"],
            "title": f"{panel} {plan_cfg['title']}",
            "context": {"fire_type": "goaf_spontaneous_combustion", "as_of": ev["as_of"], "explosion_risk": "unknown", "sealed_at": sealed_at},
            "steps": steps,
            "basis": [{"document": plan_cfg["basis"]["document"], "article": a, "source": "index", "note": None} for a in plan_cfg["basis"]["articles"]],
            "panel": panel,
        }
    result = {
        "panel": panel,
        "as_of": ev["as_of"],
        "ready": ready,
        "ready_name": "具备启封条件" if ready else "不具备启封条件",
        "rows": rows,
        "total_count": len(rows),
        "met_count": len(rows) - len(unmet),
        "unmet_count": len(unmet),
        "unmet_labels": "、".join(r["label"] for r in unmet),
        "sealed_at": sealed_at,
        "plan": plan,
        "steps_view": steps_view,
        "step_count": len(steps_view),
        "data_quality": notes,
    }
    params = {k: cfg[k] for k in ("window_h", "min_sealed_h", "max_o2_pct", "max_co_ppm", "max_co_rise_ppm_h", "max_c2h4_ppm", "max_temp_c")}
    warnings = ["启封条件和方案步骤为开发初稿（占位值），待专家评审"] if cfg["status"] == "placeholder" else []
    return result, params, warnings + notes


if __name__ == "__main__":
    main(VERSION, run)
