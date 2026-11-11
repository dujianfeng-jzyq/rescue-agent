#!/usr/bin/env python3
"""按底图的巷道线给撤人路线算途经点（mine_map.yaml 的 roadways.<编号>.via），让路线沿巷道走，不画成节点之间的直线。

做法：把底图 geometry.json 的线段（缺省只用图层 0，即巷道轮廓）画到栅格上（--cell 格宽，向外加宽 --grow 格，补上 T 形接头和小缺口），
网络节点按映射表的位置吸附到最近的线（超过 --snap 不吸附、该巷道跳过），沿线所在的格子求最短路（A*），
化简（--simplify）后去掉首尾两点，写成途经点。巷道是双线画的，路线会沿其中一条边线走（示意足够）。

已有 via 的巷道不改（除非 --overwrite）；有名称映射的巷道保留名称，改成 {name, via} 写法。
结果是示意，未经现场核对；映射表本身在 var/map/（git 忽略，真实矿井图不进仓库）。

用法：
    python3 scripts/map/route_via.py --sim [--map var/map/mine_map.yaml] [--geometry var/map/geometry.json] [--dry-run]
    python3 scripts/map/route_via.py --network network.json ...
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_network(args) -> dict:
    if args.network:
        return json.loads(Path(args.network).read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT / "mcp/mine-data-sim/src"))
    sys.path.insert(0, str(ROOT / "mcp/contract/src"))
    from rescue_mine_data_sim.mine import NODES, ROADWAYS  # noqa: PLC0415  按 RESCUE_SIM_PANEL 取编号

    return {"nodes": [n.model_dump(mode="json") for n in NODES], "roadways": [r.model_dump(mode="json") for r in ROADWAYS]}


class Raster:
    """把线段画到栅格上（格宽 cell，向外加宽 grow 格，补上 T 形接头和小缺口），在线所在的格子里用 A* 找路。"""

    def __init__(self, segments: list[tuple[float, float, float, float]], bbox: list[float], cell: float, grow: int) -> None:
        self.cell, self.x0, self.y0 = cell, bbox[0] - 2 * cell, bbox[1] - 2 * cell
        self.w = int((bbox[2] - self.x0) / cell) + 4
        self.h = int((bbox[3] - self.y0) / cell) + 4
        self.on = bytearray(self.w * self.h)
        for x1, y1, x2, y2 in segments:
            n = max(1, int(math.dist((x1, y1), (x2, y2)) / (cell / 2)))
            for k in range(n + 1):
                cx, cy = self.cell_of(x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n)
                for dx in range(-grow, grow + 1):
                    for dy in range(-grow, grow + 1):
                        if 0 <= cx + dx < self.w and 0 <= cy + dy < self.h:
                            self.on[(cy + dy) * self.w + cx + dx] = 1

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        return int((x - self.x0) / self.cell), int((y - self.y0) / self.cell)

    def center(self, c: int) -> tuple[float, float]:
        return self.x0 + (c % self.w + 0.5) * self.cell, self.y0 + (c // self.w + 0.5) * self.cell

    def nearest(self, p: tuple[float, float], limit: float) -> tuple[int, float] | None:
        cx, cy = self.cell_of(*p)
        r = int(limit / self.cell) + 1
        best = None
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                x, y = cx + dx, cy + dy
                if 0 <= x < self.w and 0 <= y < self.h and self.on[y * self.w + x]:
                    d = math.dist(self.center(y * self.w + x), p)
                    if d <= limit and (best is None or d < best[1]):
                        best = (y * self.w + x, d)
        return best

    def path(self, a: int, b: int) -> list[int] | None:
        bx, by = b % self.w, b // self.w
        g, prev = {a: 0.0}, {}
        q = [(0.0, a)]
        steps = [(dx, dy, math.hypot(dx, dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
        while q:
            _, u = heapq.heappop(q)
            if u == b:
                out = [b]
                while out[-1] != a:
                    out.append(prev[out[-1]])
                return out[::-1]
            ux, uy = u % self.w, u // self.w
            for dx, dy, w in steps:
                x, y = ux + dx, uy + dy
                if not (0 <= x < self.w and 0 <= y < self.h):
                    continue
                v = y * self.w + x
                if not self.on[v]:
                    continue
                nd = g[u] + w
                if nd < g.get(v, math.inf):
                    g[v], prev[v] = nd, u
                    heapq.heappush(q, (nd + math.hypot(bx - x, by - y), v))
        return None


def simplify(pts: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Douglas-Peucker。"""
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    L = math.dist(pts[0], pts[-1]) or 1e-9
    far, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        x, y = pts[i]
        d = abs((x2 - x1) * (y1 - y) - (x1 - x) * (y2 - y1)) / L
        if d > far:
            far, idx = d, i
    if far <= eps:
        return [pts[0], pts[-1]]
    return simplify(pts[: idx + 1], eps)[:-1] + simplify(pts[idx:], eps)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sim", action="store_true", help="模拟矿井的网络（RESCUE_SIM_PANEL 决定编号）")
    src.add_argument("--network", help="矿井网络 JSON（rescue-mine get_network）")
    ap.add_argument("--map", default=str(ROOT / "var/map/mine_map.yaml"))
    ap.add_argument("--geometry", default=str(ROOT / "var/map/geometry.json"))
    ap.add_argument("--layers", default="0", help="用哪些图层的线（逗号分隔，缺省 0）")
    ap.add_argument("--cell", type=float, default=0.5, help="栅格格宽（地图坐标）")
    ap.add_argument("--grow", type=int, default=8, help="线向外加宽的格数（补 T 形接头、巷道交叉口的缺口；缺省 8 格 = 4 个地图单位）")
    ap.add_argument("--snap", type=float, default=12.0, help="节点吸附到线上的最大距离")
    ap.add_argument("--simplify", type=float, default=1.5, help="折线化简的容差")
    ap.add_argument("--overwrite", action="store_true", help="已有 via 的巷道也重算")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = ap.parse_args()

    geo = json.loads(Path(args.geometry).read_text(encoding="utf-8"))
    segs = []
    for name in args.layers.split(","):
        for ln in geo["layers"].get(name, {}).get("lines", []):
            p = ln["p"]
            segs += [(p[i], p[i + 1], p[i + 2], p[i + 3]) for i in range(0, len(p) - 2, 2)]
    g = Raster(segs, geo["bbox"], args.cell, args.grow)
    mpath = Path(args.map)
    raw = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
    nodes = {k: tuple(v["at"]) for k, v in (raw.get("nodes") or {}).items()}
    roads = raw.setdefault("roadways", {}) or {}
    net = load_network(args)
    snapped: dict[str, int] = {}
    for nid, at in nodes.items():
        hit = g.nearest(at, args.snap)
        if hit:
            snapped[nid] = hit[0]
        else:
            print(f"跳过节点 {nid}：{args.snap} 以内没有巷道线")
    changed = 0
    for r in net["roadways"]:
        rid, a, b = r["id"], r["from_node"], r["to_node"]
        cur = roads.get(rid)
        if isinstance(cur, dict) and cur.get("via") and not args.overwrite:
            continue
        if a not in snapped or b not in snapped:
            print(f"跳过巷道 {rid}：节点 {a}/{b} 没有吸附到线上")
            continue
        path = g.path(snapped[a], snapped[b])
        if path is None:
            print(f"跳过巷道 {rid}：{a} 和 {b} 在底图上不连通")
            continue
        pts = simplify([nodes[a]] + [g.center(i) for i in path] + [nodes[b]], args.simplify)
        via = [[round(x, 2), round(y, 2)] for x, y in pts[1:-1]]
        if not via:
            continue
        name = cur if isinstance(cur, str) else (cur or {}).get("name") if isinstance(cur, dict) else None
        roads[rid] = {**({"name": name} if name else {}), "via": via}
        changed += 1
        print(f"{rid}（{r.get('name', '')}）：{a} → {b}，途经点 {len(via)} 个")
    raw["roadways"] = roads
    if args.dry_run or not changed:
        print(f"共 {changed} 条巷道有新的途经点{'（未写入）' if args.dry_run else ''}")
        return 0
    mpath.with_suffix(mpath.suffix + ".bak").write_text(mpath.read_text(encoding="utf-8"), encoding="utf-8")
    mpath.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"已写入 {mpath}（{changed} 条巷道；原文件备份为 {mpath.name}.bak）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
