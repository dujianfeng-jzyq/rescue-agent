"""处置效果复评：对工作面全部发火指标测点逐个比较处置前后的上升速率，汇总为总体结论。

由 mcp/fire-model 的 effect.py（panel_effect）和 mcp/workflow 的 escalation.py（review_effect、counts_by_type）迁移，判据不变。
测点：采空区束管 CO、采空区测温 TEMP、非进风侧安全监控 CO（采集清单 effect，窗口从 since 之前 baseline_h 小时到现在）。
火区台账里本工作面已封闭时，封闭后一直读不到数的安全监控测点（密闭内断电）不参与复评，列在 sealed_off（2026-11-10 演示复看：
封闭后这两个测点一直算“观测时间不足”）。TODO(专家): 封闭范围内哪些测点停用，按本矿封闭方案定。

输入：{"evidence": <effect 证据，含 since>, "options": {"since_source": <处置开始时间的来源说明>, "treated": <是否采取过处置，缺省 true>}}。
treated 为 false（指令台账里只有加密监测、没有防灭火或封闭方案）时，since 是观察起点而不是处置开始：判据不变，
结论和测点数改用“未采取处置：……”的写法（2026-11-11 演示复看：只开了加密监测，复评却写“处置开始：加密监测”“处置效果：恶化”，
读的人会以为措施没起作用，其实没有处置）。结构化的 overall、counts 不变（下游按它们判断是否再问、是否建议升级）。
总体结论、各结论测点数、按类型分组的测点数、下次复评时刻都在这里算好，照抄，不要自己数或推算。
"""

from datetime import datetime, timedelta

from _effect import evaluate, overall
from _evidence import as_of, sensors
from _quality import check_series
from _skill import evidence, load_reference, main

VERSION = "effect/1.0.0"
# 文字汇总用的短名（不带标点，便于照抄）；结构化的 counts 仍用完整结论名
SHORT = {"已受控": "已受控", "需继续处置": "需继续处置", "恶化": "恶化", "观测时间不足，暂不下结论": "观测时间不足"}
# 未采取处置时的写法：同一判据，说的是“趋势”而不是“处置效果”
UNTREATED_SHORT = {"已受控": "停止上升", "需继续处置": "上升减缓", "恶化": "恶化", "观测时间不足": "观测时间不足"}
UNTREATED_OVERALL = {"controlled": "未采取处置：停止上升", "continuing": "未采取处置：上升减缓", "worsening": "未采取处置：趋势恶化", "insufficient": "未采取处置：观测时间不足，暂不下结论"}
LABELS = {
    True: {"since_label": "处置开始", "before_label": "处置前", "after_label": "处置后", "at_label": "处置时"},
    False: {"since_label": "观察起点（未采取处置）", "before_label": "起点前", "after_label": "起点后", "at_label": "起点时"},
}
SOURCE_NAMES = {"bundle_tube": "采空区束管", "temperature": "采空区测温", "safety_monitor": "安全监控"}
METHOD = "逐测点处置前后速率比较后汇总（任一恶化→恶化；全部已受控→已受控；否则需继续处置；观测不足的测点不参与）。判据为演示占位值，需按本矿制度审定"


def sealed_at(ev):
    """火区台账里本工作面已封闭火区的封闭时间（没有台账或未封闭为 None）。"""
    times = [datetime.fromisoformat(str(a["sealed_at"])) for a in ev.get("fire_areas") or [] if a.get("panel") == ev["panel"] and a.get("status") == "sealed" and a.get("sealed_at")]
    return max(times) if times else None


def sealed_dark(ev, tg, now):
    """评估测点里，火区已封闭后一直读不到数的安全监控测点（密闭内断电）：不参与复评，单独列出。束管、测温缺数照常算观测不足。"""
    at = sealed_at(ev)
    if at is None:
        return set()
    return {s["id"] for s, m in tg if s["source"] == "safety_monitor" and not any(r.get("value") is not None for r in readings(ev, s["id"], m, at, now))}


def targets(ev):
    """（测点, 参数）：采空区束管 CO、采空区测温、非进风侧安全监控 CO。"""
    intake = {r["id"] for r in ev["network"]["roadways"] if r["kind"] == "intake"}
    out = []
    for s in sensors(ev):
        if ev["panel"] not in s["id"]:
            continue
        if s["source"] == "bundle_tube" and "CO" in s["measurands"]:
            out.append((s, "CO"))
        elif s["source"] == "temperature" and "TEMP" in s["measurands"]:
            out.append((s, "TEMP"))
        elif s["source"] == "safety_monitor" and "CO" in s["measurands"] and s.get("roadway_id") not in intake:
            out.append((s, "CO"))
    return out


def readings(ev, sensor_id, measurand, start, end):
    """证据里某测点某参数在 [start, end] 内的读数（各测点组去重），按时间排序。"""
    seen = {}
    for g in ev["groups"].values():
        if measurand not in g["measurands"].get(sensor_id, []):
            continue
        for r in g["readings"]:
            if r["sensor_id"] == sensor_id and r["measurand"] == measurand and start <= datetime.fromisoformat(r["ts"]) <= end:
                seen.setdefault(r["ts"], r)
    return [seen[k] for k in sorted(seen, key=datetime.fromisoformat)]


def run(data, _args):
    ev = evidence(data)
    cfg = load_reference("effect.yaml")
    if not ev.get("since"):
        raise ValueError("证据里没有处置开始时间 since（采集 effect 时要给出）")
    if "network" not in ev:
        raise ValueError("证据里没有通风网络（采集清单 effect 应取 network）")
    since, now = datetime.fromisoformat(ev["since"]), as_of(ev)
    start = since - timedelta(hours=cfg["baseline_h"])
    windows = [g["window_h"] for g in ev["groups"].values() if g["sensors"]]
    if windows and min(windows) * 3600 + 1 < (now - start).total_seconds():
        raise ValueError(f"证据的时间窗口不足：需要从处置前 {cfg['baseline_h']} 小时取到现在，检查采集清单的 before_since_h")
    tg = targets(ev)
    dark = sealed_dark(ev, tg, now)
    tg = [(s, m) for s, m in tg if s["id"] not in dark]
    if not tg:
        raise ValueError(f"工作面 {ev['panel']} 没有可用于效果评估的发火指标测点")
    warnings, points = [], []
    for s, m in tg:
        sid = s["id"]

        def usable(meas, a, b, sibling=None, sid=sid):
            sib = readings(ev, sid, sibling, a, b) if sibling else None
            rep = check_series(f"{sid}/{meas}", readings(ev, sid, meas, a, b), sib)
            return rep.usable, rep.flags

        before, f1 = usable(m, start, since)
        after, f2 = usable(m, since, now)
        warnings += f1 + f2
        markers = {}
        for gas in ("C2H4", "C2H2"):
            pre, _ = usable(gas, start, since, "CO")
            post, _ = usable(gas, since, now, "CO")
            markers[gas] = (pre, post)
        out = evaluate(since, now, before, after, markers, cfg, cfg["flat_rate"][m], cfg["early_worsening"]["min_increase"].get(m))
        points.append({"sensor_id": sid, "measurand": m, "source": s["source"], **out})
    res = overall(points, since, now, cfg["review_interval_h"])
    options = data.get("options") or {}
    treated = options.get("treated", True) is not False
    short = (lambda k: SHORT.get(k, k)) if treated else (lambda k: UNTREATED_SHORT.get(SHORT.get(k, k), SHORT.get(k, k)))
    if not treated:
        # 依据里的“处置前/处置后”改成“起点前/起点后”，结论名换成趋势的说法（判据不变）
        def words(t):
            for a, b in (("处置后", "起点后"), ("处置前", "起点前"), ("需继续处置", "上升减缓"), ("已受控", "停止上升")):
                t = t.replace(a, b)
            return t

        res = {**res, "basis": [words(b) for b in res.get("basis") or []]}
        points = [{**p, "basis": [words(b) for b in p["basis"]], "effect_name": UNTREATED_SHORT.get(SHORT.get(p["effect_name"], p["effect_name"]), p["effect_name"])} for p in points]
    groups = {}
    for p in points:
        key = (SOURCE_NAMES.get(p["source"], "其他"), p["measurand"])
        c = groups.setdefault(key, {})
        c[p["effect_name"]] = c.get(p["effect_name"], 0) + 1
    by_type = [{"type": t, "measurand": mm, "total": sum(c.values()), "counts": c, "counts_text": "、".join(f"{short(k)} {v} 个" for k, v in c.items())} for (t, mm), c in groups.items()]
    result = {
        "panel": ev["panel"],
        **res,
        **({} if treated else {"overall_name": UNTREATED_OVERALL[res["overall"]]}),
        "treated": treated,
        **LABELS[treated],
        "since_source": options.get("since_source") or "调用方给出",
        "counts_text": "、".join(f"{short(k)} {v} 个" for k, v in res["counts"].items()),
        "counts_by_type": by_type,
        # 逐点文字的“处置前/处置后”或“起点前/起点后”放在每个点上：模板里点自己的 effect 字段会遮住外层的 effect
        "points": [
            {k: p[k] for k in ("sensor_id", "measurand", "effect", "effect_name", "value_at_since", "latest", "change_since", "rate_before_per_hour", "rate_after_per_hour", "rate_reduction_pct", "new_marker_gases", "basis")}
            | {"new_marker_gases_text": "、".join(p["new_marker_gases"])}
            | {k: LABELS[treated][k] for k in ("before_label", "after_label", "at_label")}
            for p in points
        ],
        "sealed_off": sorted(dark),  # 火区封闭后断电、不参与复评的安全监控测点
        "sealed_off_text": "、".join(sorted(dark)),
        "escalation_suggested": res["overall"] == "worsening",
        "method": METHOD,
    }
    params = {k: cfg[k] for k in ("baseline_h", "min_observe_h", "early_worsening", "review_interval_h", "controlled_rate", "slowdown_ratio", "flat_rate", "markers")}
    return result, params, sorted(set(warnings))


if __name__ == "__main__":
    main(VERSION, run)
