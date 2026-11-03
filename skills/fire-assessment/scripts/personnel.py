"""受威胁人员：有害气体沿风流从源头巷道向下风侧蔓延的范围、到达时间，受威胁人员按巷道分组，附避灾路线。

由 mcp/ventnet 的 network.py（threatened、downstream、escape_route）迁移，算法和参数不变。
方法是网络拓扑 + 设计风量估算（未做风量解算），到达时间仅供参考。

输入：{"evidence": <证据内容，含 network、personnel>, "warning": <warning.py 的输出>, "options": {"include_personnel": bool}}。
预警等级未达到 assessment.yaml 的 personnel_level_at_least 且没有要求时不计算（computed 为 false，note 说明原因）。
各组人数和合计都在这里算好，照抄，不要自己数。撤人按预案立即执行，不等待本结果。
"""

import itertools
from collections import deque

import networkx as nx
from _skill import evidence, load_reference, main, record_result
from _warning import NAMES, level_at_least

VERSION = "personnel/1.0.0"
METHOD = "网络拓扑 + 设计风量估算（未做风量解算），到达时间仅供参考"


def air_velocity_m_s(design_airflow_m3_min, p):
    if not design_airflow_m3_min:
        return p["default_air_velocity_m_s"]
    return max(p["min_air_velocity_m_s"], design_airflow_m3_min / 60 / p["default_section_m2"])


def downstream(net, source_roadway, p):
    """沿风流方向找出源头下风侧的全部巷道及到达时间（最短传播时间）。"""
    by_id = {r["id"]: r for r in net["roadways"]}
    if source_roadway not in by_id:
        raise ValueError(f"未知巷道：{source_roadway}")
    out_edges = {}
    for r in net["roadways"]:
        out_edges.setdefault(r["from_node"], []).append(r)
    src = by_id[source_roadway]
    best = {src["id"]: 0.0}
    t_src_end = src["length_m"] / air_velocity_m_s(src.get("design_airflow_m3_min"), p) / 60
    queue = deque([(src["to_node"], t_src_end)])
    while queue:
        node, t = queue.popleft()
        for r in out_edges.get(node, []):
            if r["id"] in best and best[r["id"]] <= t:
                continue
            best[r["id"]] = t
            queue.append((r["to_node"], t + r["length_m"] / air_velocity_m_s(r.get("design_airflow_m3_min"), p) / 60))
    return sorted(((rid, round(t, 1)) for rid, t in best.items()), key=lambda e: e[1])


def escape_route(net, from_roadway, avoid, p):
    """从某巷道出发到地面出口或避难硐室的最短路线，避开受污染巷道（起点巷道除外）。优先走进风巷道。"""
    by_id = {r["id"]: r for r in net["roadways"]}
    g = nx.Graph()
    for r in net["roadways"]:
        if r["kind"] == "goaf":
            continue
        if r["id"] in avoid and r["id"] != from_roadway:
            continue
        w = r["length_m"] * (1.0 if r["kind"] == "intake" else p["non_intake_penalty"])
        g.add_edge(r["from_node"], r["to_node"], weight=w, roadway=r["id"], length=r["length_m"])
    start = by_id[from_roadway]
    targets = [n["id"] for n in net["nodes"] if n.get("is_surface") or n.get("is_refuge")]
    best = None
    for origin in (start["from_node"], start["to_node"]):
        if origin not in g:
            continue
        for tgt in targets:
            if tgt not in g:
                continue
            try:
                path = nx.shortest_path(g, origin, tgt, weight="weight")
            except nx.NetworkXNoPath:
                continue
            length = sum(g.edges[a, b]["length"] for a, b in itertools.pairwise(path)) + start["length_m"] / 2
            if best is None or length < best["length_m"]:
                best = {
                    "nodes": path,
                    "roadways": [g.edges[a, b]["roadway"] for a, b in itertools.pairwise(path)],
                    "length_m": round(length),
                    "target": tgt,
                }
    if best is None:
        return {"from_roadway": from_roadway, "reachable": False, "note": "避开受污染巷道后无可达出口或避难硐室，需人工研判"}
    nodes = {n["id"]: n for n in net["nodes"]}
    best.update(
        from_roadway=from_roadway,
        reachable=True,
        target_name=nodes[best["target"]]["name"],
        walk_min=round(best["length_m"] / p["walk_speed_m_s"] / 60, 1),
        walk_with_self_rescuer_min=round(best["length_m"] / p["self_rescuer_speed_m_s"] / 60, 1),
    )
    return best


def threatened(net, source_roadway, people, p):
    exposures = dict(downstream(net, source_roadway, p))
    contaminated = set(exposures)
    groups, others = {}, {}
    for person in people:
        (groups if person["roadway_id"] in contaminated else others).setdefault(person["roadway_id"], []).append(person["person_id"])
    result = []
    for roadway, persons in sorted(groups.items(), key=lambda kv: exposures[kv[0]]):
        result.append({
            "roadway_id": roadway, "persons": persons, "persons_text": "、".join(persons), "count": len(persons),
            "arrival_min": exposures[roadway], "escape_route": escape_route(net, roadway, contaminated, p),
        })  # fmt: skip
    return {
        "source_roadway": source_roadway,
        "contaminated_roadways": [{"roadway_id": r, "arrival_min": t} for r, t in sorted(exposures.items(), key=lambda kv: kv[1])],
        "threatened": result,
        "total_threatened": sum(len(x["persons"]) for x in result),
        "not_threatened": [{"roadway_id": r, "persons": ps, "count": len(ps)} for r, ps in sorted(others.items())],
        "total_not_threatened": sum(len(ps) for ps in others.values()),
        "total_underground": len(people),
        "method": METHOD,
    }


def source_roadway(net, panel, kind):
    for r in net["roadways"]:
        if r["kind"] == kind and panel in r["id"]:
            return r["id"]
    raise ValueError(f"网络中找不到工作面 {panel} 的 {kind} 巷道，不能计算受威胁人员")


def run(data, _args):
    ev = evidence(data)
    w = record_result(data, "warning")
    cfg = load_reference("assessment.yaml")
    p = load_reference("personnel.yaml")
    include = bool((data.get("options") or {}).get("include_personnel"))
    floor = cfg["personnel_level_at_least"]
    params = {"personnel_level_at_least": floor, "personnel_source_kind": cfg["personnel_source_kind"], **{k: v for k, v in p.items() if k != "reviewed_by"}}
    base = {"panel": ev["panel"], "as_of": ev["as_of"]}
    if not (include or level_at_least(w["level"], floor)):
        return {**base, "computed": False, "note": f"预警等级未达到{NAMES[floor]}，未计算受威胁人员"}, params, []
    if "network" not in ev or "personnel" not in ev:
        raise ValueError("证据里没有通风网络或人员位置（采集清单 assessment 应取 network、personnel）")
    src = source_roadway(ev["network"], ev["panel"], cfg["personnel_source_kind"])
    out = threatened(ev["network"], src, ev["personnel"]["people"], p)
    return {**base, "computed": True, "note": None, **out}, params, ["到达时间按设计风量估算（未做风量解算），仅供参考"]


if __name__ == "__main__":
    main(VERSION, run)
