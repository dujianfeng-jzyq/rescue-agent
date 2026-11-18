"""外部火灾推演服务的结果：判读、选定蔓延范围显示哪一种，并与我方受污染巷道估算比较（docs/design-v7-fire-sim.md 第 3.1 节、附录 A.3、A.4）。

输入：{"evidence": <证据内容，含 network>, "personnel": <personnel.py 的输出>, "fire_sim_request": <fire_sim_request.py 的输出>,
       "fire_sim": <证据插件调用 fire_sim 得到的证据内容：{result | error}>,
       "route_compare": <可选，route_compare.py 的输出：有它时按每组显示的避灾路线核对危险巷道，没有时按我方路线>}。
输出：
- roadways：按我方巷道（拆开的两段合并）给出 CO 到达时刻（≥ co_reach_ppm）、危险时刻（≥ co_danger_ppm）、峰值、风向反转；
- comparison：与我方受污染巷道（personnel 的 contaminated_roadways）比较：两者都有、只有推演有、只有我方有，及到达时间差；
- groups：每组受威胁人员所在巷道的推演到达和危险时刻，以及我方避灾路线经过的、推演中会达到危险浓度的巷道；
- extra_people：我方判为不受威胁、推演中 CO 会到达其所在巷道的人员（单独列出，由人核对）；
- lines：完整的判读和比较整句（存档、复盘用）；
- display_source / display_lines：界面和报告显示哪一种蔓延范围（外部为主、我方兜底）及显示的整句：推演可用时 service，
  只显示推演结果（不写与我方估算的比较）；推演不可用时 ours，写明原因，蔓延范围和到达时间按我方估算显示。
全部在这里算好，照抄。

两种方法的假设不同：我方按设计风量沿风流估算，源头取工作面巷道；推演按风阻解算风量、模拟巷道内明火，火源按参数放在漏风通道上。
所以源头附近的差别是预期中会出现的，lines 里写明两边的源头。时刻从推演开始计，单位分钟；推演结束仍未到达的为 null。
"""

from _route import roadway_of
from _skill import evidence, load_reference, main, record_result

VERSION = "fire_sim_compare/1.1.1"
METHOD = "外部服务推演结果按巷道合并（两段取最早到达、最大浓度），按 CO 阈值判读后与我方受污染巷道比较（试验性）"


def minutes(t_s):
    return None if t_s is None else round(t_s / 60, 1)


def num(v):
    """显示用：整数值不带 .0。"""
    return int(v) if float(v).is_integer() else v


def first_at_least(times, values, threshold):
    return next((t for t, v in zip(times, values, strict=True) if v >= threshold), None)


def per_roadway(res, net, p):
    """推演结果（分段）→ 我方巷道：两段里取最早的到达、危险时刻和最大的峰值；任一段风向反转或停风即算。"""
    times = res["times_s"]
    names = {r["id"]: r["name"] for r in net["roadways"]}
    merged: dict[str, dict] = {}
    for b in res["branches"]:
        rid = roadway_of(b["id"])
        reach = first_at_least(times, b["co_max_ppm"], p["co_reach_ppm"])
        danger = first_at_least(times, b["co_max_ppm"], p["co_danger_ppm"])
        m = merged.setdefault(
            rid,
            {
                "reach_s": None,
                "danger_s": None,
                "peak_ppm": 0.0,
                "air_reversed": False,
                "air_stopped": False,
            },
        )
        for key, t in (("reach_s", reach), ("danger_s", danger)):
            if t is not None and (m[key] is None or t < m[key]):
                m[key] = t
        m["peak_ppm"] = max(m["peak_ppm"], b["co_peak_ppm"])
        m["air_reversed"] = m["air_reversed"] or b["air_reversed"]
        m["air_stopped"] = m["air_stopped"] or b["air_stopped"]
    out = []
    for rid in sorted(
        merged,
        key=lambda r: (merged[r]["reach_s"] is None, merged[r]["reach_s"] or 0, r),
    ):
        m = merged[rid]
        out.append({
            "roadway_id": rid, "name": names.get(rid, rid), "reach_min": minutes(m["reach_s"]), "danger_min": minutes(m["danger_s"]),
            "peak_ppm": m["peak_ppm"], "air_reversed": m["air_reversed"], "air_stopped": m["air_stopped"],
        })  # fmt: skip
    return out


def compare_roadways(sim, per, goaf):
    """采空区漏风通道不是人员通行的巷道，我方估算也不沿它蔓延：不参与比较（仍在 roadways 里列出）。"""
    ours = {c["roadway_id"]: c["arrival_min"] for c in per["contaminated_roadways"] if c["roadway_id"] not in goaf}
    reached = {r["roadway_id"]: r for r in sim if r["reach_min"] is not None and r["roadway_id"] not in goaf}
    both = [
        {"roadway_id": rid, "ours_min": ours[rid], "sim_min": reached[rid]["reach_min"], "diff_min": round(reached[rid]["reach_min"] - ours[rid], 1)}
        for rid in ours if rid in reached
    ]  # fmt: skip
    sim_only = [{"roadway_id": rid, "sim_min": r["reach_min"]} for rid, r in reached.items() if rid not in ours]
    ours_only = [{"roadway_id": rid, "ours_min": t} for rid, t in ours.items() if rid not in reached]
    return {"both": both, "sim_only": sim_only, "ours_only": ours_only}


ROUTE_SOURCES = {"service": "外部路径规划服务", "ours": "本系统计算"}


def groups_view(per, sim_by, horizon_min, shown_routes):
    """shown_routes：每组显示的避灾路线（route_compare 的 display）；没有的组按我方路线。"""
    groups = []
    for g in per["threatened"]:
        s = sim_by.get(g["roadway_id"], {})
        er = shown_routes.get(g["roadway_id"]) or {**g["escape_route"], "source": "ours"}
        hits = []
        if er.get("reachable"):
            hits = [
                {"roadway_id": r, "danger_min": sim_by[r]["danger_min"]}
                for r in er["roadways"]
                if sim_by.get(r, {}).get("danger_min") is not None
            ]
        groups.append({
            "roadway_id": g["roadway_id"], "count": g["count"], "ours_arrival_min": g["arrival_min"],
            "sim_reach_min": s.get("reach_min"), "sim_danger_min": s.get("danger_min"), "sim_peak_ppm": s.get("peak_ppm", 0.0),
            "route_source": er["source"], "route_reachable": bool(er.get("reachable")), "route_danger_roadways": hits,
        })  # fmt: skip
    extra = []
    for g in per["not_threatened"]:
        s = sim_by.get(g["roadway_id"], {})
        if s.get("reach_min") is not None:
            extra.append(
                {
                    "roadway_id": g["roadway_id"],
                    "count": g["count"],
                    "sim_reach_min": s["reach_min"],
                    "sim_danger_min": s.get("danger_min"),
                }
            )
    return groups, extra


def exposure(reach, danger, horizon_min, p):
    """某巷道的推演结果写成一句：何时达到“到达”浓度、何时达到“危险”浓度。"""
    if reach is None:
        return f"推演 {num(horizon_min)} 分钟内 CO 未达到 {p['co_reach_ppm']} ppm"
    s = f"CO 第 {num(reach)} 分钟达到 {p['co_reach_ppm']} ppm"
    return s + (
        f"、第 {num(danger)} 分钟达到 {p['co_danger_ppm']} ppm"
        if danger is not None
        else f"，{num(horizon_min)} 分钟内未达到 {p['co_danger_ppm']} ppm"
    )


def head_lines(req, per, sim, horizon_min, p, with_ours):
    reached = [r for r in sim if r["reach_min"] is not None]
    source = f"推演火源：{req['fire_roadway_name']}（{req['fire_roadway']}，位置 {req['fire_position']}）"
    head = (
        source + (f"；我方估算源头：{per['source_roadway']}。" if with_ours else "。")
        + f"推演 {num(horizon_min)} 分钟内 CO 达到 {p['co_reach_ppm']} ppm 的巷道 {len(reached)} 条"
    )  # fmt: skip
    if reached:
        head += "：" + "、".join(f"{r['roadway_id']}（第 {num(r['reach_min'])} 分钟，峰值 {num(r['peak_ppm'])} ppm）" for r in reached)
    flips = [r["roadway_id"] for r in sim if r["air_reversed"] or r["air_stopped"]]
    return [head, f"风流反转或停风的巷道：{'、'.join(flips)}" if flips else "推演中没有巷道风流反转或停风"]


def people_lines(groups, extra, horizon_min, p, with_source):
    """with_source：写不写避灾路线的来源（存档写；界面和报告只用颜色区分来源，不写）。"""
    out = []
    for g in groups:
        s = f"{g['roadway_id']}（{g['count']} 人）：{exposure(g['sim_reach_min'], g['sim_danger_min'], horizon_min, p)}"
        if g["route_danger_roadways"]:
            s += (
                (f"；避灾路线（{ROUTE_SOURCES[g['route_source']]}）经过的 " if with_source else "；避灾路线经过的 ")
                + "、".join(f"{h['roadway_id']}（第 {num(h['danger_min'])} 分钟）" for h in g["route_danger_roadways"])
                + f"在推演中会达到 {p['co_danger_ppm']} ppm"
            )
        out.append(s)
    for x in extra:
        out.append(
            f"{x['roadway_id']}（{x['count']} 人，不在受威胁名单内）：{exposure(x['sim_reach_min'], x['sim_danger_min'], horizon_min, p)}，请核对"
        )
    return out


def lines_for(req, per, sim, cmp_, groups, extra, horizon_min, p):
    """完整的判读和比较（存档、复盘用）。"""
    out = head_lines(req, per, sim, horizon_min, p, with_ours=True)
    if cmp_["sim_only"]:
        out.append(
            "推演中 CO 会到达、我方未列为受污染的巷道："
            + "、".join(f"{x['roadway_id']}（第 {num(x['sim_min'])} 分钟）" for x in cmp_["sim_only"])
        )
    if cmp_["ours_only"]:
        out.append(
            f"我方列为受污染、推演 {num(horizon_min)} 分钟内 CO 未达到 {p['co_reach_ppm']} ppm 的巷道："
            + "、".join(x["roadway_id"] for x in cmp_["ours_only"])
        )
    return out + people_lines(groups, extra, horizon_min, p, with_source=True)


def display_lines_for(req, per, sim, groups, extra, horizon_min, p):
    """界面和报告显示的整句：只写推演结果（第 3.1 节），不写与我方估算的比较，也不写路线来源（界面只用颜色区分来源）。"""
    return head_lines(req, per, sim, horizon_min, p, with_ours=False) + people_lines(groups, extra, horizon_min, p, with_source=False)


def shown_routes_of(data):
    """可选输入 route_compare：每组显示的避灾路线（route_compare 的 display）。"""
    rc = data.get("route_compare")
    if rc is None:
        return {}
    res = record_result(data, "route_compare")
    return {g["roadway_id"]: g["display"] for g in res.get("groups") or []}


def run(data, _args):
    ev = evidence(data)
    per = record_result(data, "personnel")
    req = record_result(data, "fire_sim_request")
    p = load_reference("fire_sim.yaml")
    params = {k: p[k] for k in ("co_reach_ppm", "co_danger_ppm")}
    base = {
        "panel": ev["panel"],
        "as_of": ev["as_of"],
        "experimental": True,
        "method": METHOD,
    }
    empty = {
        "roadways": [],
        "comparison": None,
        "groups": [],
        "extra_people": [],
        "counts": {},
        "lines": [],
        "display_source": None,
        "display_lines": [],
    }
    if not req.get("needed"):
        return (
            {
                **base,
                "computed": False,
                "status": "not_needed",
                "note": req.get("note"),
                "horizon_min": None,
                **empty,
            },
            params,
            [],
        )
    sim_ev = data.get("fire_sim")
    if not isinstance(sim_ev, dict) or ("result" not in sim_ev and "error" not in sim_ev):
        raise ValueError("输入缺少外部服务的调用结果 fire_sim（证据插件调用 fire_sim 得到的证据内容，含 result 或 error）")
    res = sim_ev.get("result")
    error = sim_ev.get("error") or (None if res and res.get("available") else (res or {}).get("error") or "服务不可用")
    if error:
        line = f"外部火灾推演服务不可用：{error}"
        return (
            {
                **base,
                "computed": True,
                "status": "unavailable",
                "note": error,
                "horizon_min": None,
                **empty,
                "lines": [line],
                "display_source": "ours",
                "display_lines": [f"{line}；有害气体蔓延范围和到达时间按本系统估算显示"],
            },
            params,
            [],
        )
    if not per.get("computed"):
        raise ValueError("personnel 没有计算，不能比较（请求记录与 personnel 不配套）")
    horizon_min = minutes(res["times_s"][-1])
    sim = per_roadway(res, ev["network"], p)
    sim_by = {r["roadway_id"]: r for r in sim}
    goaf = {r["id"] for r in ev["network"]["roadways"] if r["kind"] == "goaf"}
    cmp_ = compare_roadways(sim, per, goaf)
    groups, extra = groups_view(per, sim_by, horizon_min, shown_routes_of(data))
    counts = {
        "sim_reached": sum(1 for r in sim if r["reach_min"] is not None), "sim_danger": sum(1 for r in sim if r["danger_min"] is not None),
        "both": len(cmp_["both"]), "sim_only": len(cmp_["sim_only"]), "ours_only": len(cmp_["ours_only"]),
        "extra_people": sum(x["count"] for x in extra), "route_danger_groups": sum(1 for g in groups if g["route_danger_roadways"]),
    }  # fmt: skip
    out = {
        **base, "computed": True, "status": "ok", "note": None, "horizon_min": horizon_min, "roadways": sim, "comparison": cmp_,
        "groups": groups, "extra_people": extra, "counts": counts, "lines": lines_for(req, per, sim, cmp_, groups, extra, horizon_min, p),
        "display_source": "service", "display_lines": display_lines_for(req, per, sim, groups, extra, horizon_min, p),
    }  # fmt: skip
    warnings = ["外部服务推演的是巷道内明火、输入含占位值，结果是试验性的；推演可用时界面只显示推演结果，不可用时显示我方估算并写明原因"]
    return out, params, warnings


if __name__ == "__main__":
    main(VERSION, run)
