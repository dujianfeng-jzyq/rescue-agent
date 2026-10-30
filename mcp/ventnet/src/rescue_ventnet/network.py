"""通风网络分析：烟流（有害气体）蔓延范围、到达时间、受威胁人员和避灾路线。

已实现的是基于网络拓扑和设计风量的确定性分析；风量解算（Hardy Cross 等）尚未实现。
TODO(算法): 实现风量解算后，用解算风量替换设计风量，并支持模拟控风措施（调节风门、局部反风）后的风流变化。
"""

from __future__ import annotations

import itertools
from collections import deque
from dataclasses import dataclass

import networkx as nx
from rescue_contract.models import AirwayKind, MineNetwork, PersonLocation

DEFAULT_SECTION_M2 = 12.0  # 巷道断面缺省值。TODO(数据): 用图纸断面替换
WALK_SPEED_M_S = 1.0  # 正常步行
SELF_RESCUER_SPEED_M_S = 0.7  # 佩戴自救器撤离


@dataclass(frozen=True)
class Exposure:
    roadway_id: str
    arrival_min: float  # 有害气体从源头到达该巷道入口的估算时间


def air_velocity_m_s(design_airflow_m3_min: float | None, section_m2: float = DEFAULT_SECTION_M2) -> float:
    if not design_airflow_m3_min:
        return 0.5
    return max(0.2, design_airflow_m3_min / 60 / section_m2)


def downstream(net: MineNetwork, source_roadway: str) -> list[Exposure]:
    """沿风流方向找出源头下风侧的全部巷道及到达时间（最短传播时间）。"""
    by_id = {r.id: r for r in net.roadways}
    if source_roadway not in by_id:
        raise ValueError(f"未知巷道：{source_roadway}")
    out_edges: dict[str, list] = {}
    for r in net.roadways:
        out_edges.setdefault(r.from_node, []).append(r)
    src = by_id[source_roadway]
    best: dict[str, float] = {src.id: 0.0}
    t_src_end = src.length_m / air_velocity_m_s(src.design_airflow_m3_min) / 60
    queue = deque([(src.to_node, t_src_end)])
    while queue:
        node, t = queue.popleft()
        for r in out_edges.get(node, []):
            if r.id in best and best[r.id] <= t:
                continue
            best[r.id] = t
            t_end = t + r.length_m / air_velocity_m_s(r.design_airflow_m3_min) / 60
            queue.append((r.to_node, t_end))
    return sorted((Exposure(rid, round(t, 1)) for rid, t in best.items()), key=lambda e: e.arrival_min)


def escape_route(net: MineNetwork, from_roadway: str, avoid: set[str]) -> dict:
    """从某巷道出发到地面出口或避难硐室的最短路线，避开受污染巷道（起点巷道除外）。

    优先走进风巷道：进风巷道权重为长度，其他巷道加 50% 惩罚。
    """
    by_id = {r.id: r for r in net.roadways}
    g = nx.Graph()
    for r in net.roadways:
        if r.kind == AirwayKind.GOAF:
            continue
        if r.id in avoid and r.id != from_roadway:
            continue
        w = r.length_m * (1.0 if r.kind == AirwayKind.INTAKE else 1.5)
        g.add_edge(r.from_node, r.to_node, weight=w, roadway=r.id, length=r.length_m)
    start = by_id[from_roadway]
    targets = [n.id for n in net.nodes if n.is_surface or n.is_refuge]
    best = None
    for origin in (start.from_node, start.to_node):
        if origin not in g:
            continue
        for tgt in targets:
            if tgt not in g:
                continue
            try:
                path = nx.shortest_path(g, origin, tgt, weight="weight")
            except nx.NetworkXNoPath:
                continue
            length = sum(g.edges[a, b]["length"] for a, b in itertools.pairwise(path)) + start.length_m / 2
            if best is None or length < best["length_m"]:
                best = {
                    "nodes": path,
                    "roadways": [g.edges[a, b]["roadway"] for a, b in itertools.pairwise(path)],
                    "length_m": round(length),
                    "target": tgt,
                }
    if best is None:
        return {"from_roadway": from_roadway, "reachable": False, "note": "避开受污染巷道后无可达出口或避难硐室，需人工研判"}
    nodes = {n.id: n for n in net.nodes}
    best.update(
        from_roadway=from_roadway,
        reachable=True,
        target_name=nodes[best["target"]].name,
        walk_min=round(best["length_m"] / WALK_SPEED_M_S / 60, 1),
        walk_with_self_rescuer_min=round(best["length_m"] / SELF_RESCUER_SPEED_M_S / 60, 1),
    )
    return best


def threatened(net: MineNetwork, source_roadway: str, people: list[PersonLocation]) -> dict:
    """受威胁人员按巷道分组，附到达时间和避灾路线；不受威胁的人员也按巷道列出。
    各组人数和合计都在这里算好，Agent 照抄即可，不要自己数。"""
    exposures = {e.roadway_id: e.arrival_min for e in downstream(net, source_roadway)}
    contaminated = set(exposures)
    groups: dict[str, list[str]] = {}
    others: dict[str, list[str]] = {}
    for p in people:
        (groups if p.roadway_id in contaminated else others).setdefault(p.roadway_id, []).append(p.person_id)
    result = []
    for roadway, persons in sorted(groups.items(), key=lambda kv: exposures[kv[0]]):
        route = escape_route(net, roadway, contaminated)
        result.append({"roadway_id": roadway, "persons": persons, "count": len(persons), "arrival_min": exposures[roadway], "escape_route": route})
    return {
        "source_roadway": source_roadway,
        "contaminated_roadways": [{"roadway_id": r, "arrival_min": t} for r, t in sorted(exposures.items(), key=lambda kv: kv[1])],
        "threatened": result,
        "total_threatened": sum(len(x["persons"]) for x in result),
        "not_threatened": [{"roadway_id": r, "persons": ps, "count": len(ps)} for r, ps in sorted(others.items())],
        "total_not_threatened": sum(len(ps) for ps in others.values()),
        "total_underground": len(people),
        "method": "网络拓扑 + 设计风量估算（未做风量解算），到达时间仅供参考",
    }
