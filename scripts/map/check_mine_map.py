#!/usr/bin/env python3
"""地图映射表（mine_map.yaml）的一致性检查：换图、换矿、换演示矿井名称之后必须过一遍。

用法：
    python3 scripts/map/check_mine_map.py --sim                       # 模拟矿井的网络（RESCUE_SIM_PANEL 决定编号）
    python3 scripts/map/check_mine_map.py --network network.json      # 矿井网络（rescue-mine get_network 的 JSON：nodes、roadways）
    可选：--map var/map/mine_map.yaml --geometry var/map/geometry.json

检查（错误 = 路线会画不出来或画错，退出码 1；警告 = 要人工看一眼）：
  错误：网络里有节点没有位置；节点位置在图的范围外；映射表里有网络没有的节点或巷道（过期的映射）；途经点格式不对。
  警告：节点孤立（没有任何巷道）；巷道名称不在图上；折线长度与网络里的 length_m 的比例偏离其他巷道太多
        （图是示意图，比例不要求一致，只是发现某条巷道的节点位置摆错了——比如摆到了图的另一头）。
没有核对人（reviewed_by）或状态仍是 placeholder 时，界面会标“示意映射”；本脚本也会提示。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RATIO_FACTOR = 3.0  # 折线长度/网络长度的比例，偏离中位数超过这个倍数就警告


def _pt(v) -> tuple[float, float] | None:
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) and math.isfinite(x) for x in v):
        return float(v[0]), float(v[1])
    return None


def check(network: dict, mapping: dict, geometry: dict | None) -> tuple[list[str], list[str], list[str]]:
    """返回 (错误, 警告, 提示)。network: {nodes: [{id, name,...}], roadways: [{id, name, from_node, to_node, length_m}]}。"""
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    nodes_map = mapping.get("nodes") or {}
    roadways_map = mapping.get("roadways") or {}
    net_nodes = {n["id"]: n for n in network["nodes"]}
    net_roads = {r["id"]: r for r in network["roadways"]}
    bbox = geometry.get("bbox") if geometry else None
    names = set((geometry or {}).get("names", {}))

    positions: dict[str, tuple[float, float]] = {}
    for nid, node in net_nodes.items():
        v = nodes_map.get(nid)
        p = _pt((v or {}).get("at")) if isinstance(v, dict) else None
        if p is None:
            errors.append(f"节点 {nid}（{node.get('name', '')}）没有位置（nodes.{nid}.at 要写成 [x, y]）")
            continue
        if bbox:
            mx, my = (bbox[2] - bbox[0]) * 0.05, (bbox[3] - bbox[1]) * 0.05
            if not (bbox[0] - mx <= p[0] <= bbox[2] + mx and bbox[1] - my <= p[1] <= bbox[3] + my):
                errors.append(f"节点 {nid} 的位置 {list(p)} 在图的范围 {bbox} 之外")
                continue
        positions[nid] = p
    for nid in nodes_map:
        if nid not in net_nodes:
            errors.append(f"映射表里有节点 {nid}，网络里没有（过期的映射？）")
    for rid in roadways_map:
        if rid not in net_roads:
            errors.append(f"映射表里有巷道 {rid}，网络里没有（过期的映射？）")

    used = {n for r in net_roads.values() for n in (r["from_node"], r["to_node"])}
    for nid in net_nodes:
        if nid not in used:
            warnings.append(f"节点 {nid} 孤立（没有任何巷道连着它）")

    ratios: dict[str, float] = {}
    for rid, r in net_roads.items():
        spec = roadways_map.get(rid)
        via: list[tuple[float, float]] = []
        if isinstance(spec, dict):
            raw = spec.get("via") or []
            pts = [_pt(p) for p in raw]
            if any(p is None for p in pts):
                errors.append(f"巷道 {rid} 的 via 有无效的点（要写成 [[x, y], ...]）")
            else:
                via = [p for p in pts if p is not None]
            nm = spec.get("name")
            if nm and names and nm not in names:
                warnings.append(f"巷道 {rid} 的名称“{nm}”不在图上")
        elif isinstance(spec, str) and names and spec not in names:
            warnings.append(f"巷道 {rid} 的名称“{spec}”不在图上")
        a, b = positions.get(r["from_node"]), positions.get(r["to_node"])
        if a and b and r.get("length_m"):
            chain = [a, *via, b]
            length = sum(math.dist(p, q) for p, q in zip(chain, chain[1:], strict=False))
            ratios[rid] = length / r["length_m"]
    if len(ratios) >= 3:
        med = statistics.median(ratios.values())
        for rid, v in ratios.items():
            if med > 0 and not (med / RATIO_FACTOR <= v <= med * RATIO_FACTOR):
                warnings.append(f"巷道 {rid}：图上折线长度/网络长度 = {v:.3g}，与其他巷道的中位数 {med:.3g} 相差超过 {RATIO_FACTOR:g} 倍（节点位置摆错了？）")

    if mapping.get("status") == "placeholder" or not mapping.get("reviewed_by"):
        notes.append("示意映射（status 是 placeholder 或没有 reviewed_by）：界面上会标“示意映射”；核对后写上 reviewed_by")
    return errors, warnings, notes


def _sim_network() -> dict:
    sys.path.insert(0, str(ROOT / "mcp" / "mine-data-sim" / "src"))
    sys.path.insert(0, str(ROOT / "mcp" / "contract" / "src"))
    from rescue_mine_data_sim import mine  # noqa: PLC0415

    return {
        "nodes": [{"id": n.id, "name": n.name} for n in mine.NODES],
        "roadways": [{"id": r.id, "name": r.name, "from_node": r.from_node, "to_node": r.to_node, "length_m": r.length_m} for r in mine.ROADWAYS],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sim", action="store_true", help="用模拟矿井的网络")
    src.add_argument("--network", help="矿井网络的 JSON 文件")
    ap.add_argument("--map", default=str(ROOT / "var/map/mine_map.yaml"))
    ap.add_argument("--geometry", default=str(ROOT / "var/map/geometry.json"))
    a = ap.parse_args()
    network = _sim_network() if a.sim else json.loads(Path(a.network).read_text(encoding="utf-8"))
    mapping = yaml.safe_load(Path(a.map).read_text(encoding="utf-8")) or {}
    gpath = Path(a.geometry)
    geometry = json.loads(gpath.read_text(encoding="utf-8")) if gpath.exists() else None
    errors, warnings, notes = check(network, mapping, geometry)
    for m in errors:
        print(f"错误  {m}")
    for m in warnings:
        print(f"警告  {m}")
    for m in notes:
        print(f"提示  {m}")
    print(f"\n{len(network['nodes'])} 个节点、{len(network['roadways'])} 条巷道；错误 {len(errors)}，警告 {len(warnings)}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
