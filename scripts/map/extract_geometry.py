#!/usr/bin/env python3
"""从 DXF 提取通风图几何，输出前端地图用的紧凑 JSON（地图包里的 geometry.json）。

用法：
    python3 scripts/map/extract_geometry.py [--dxf var/map-work/out.dxf] [--config var/map/extract.json] [--out var/map]

DXF 来自 scripts/map/convert_dwg.sh。**真实矿井图不进仓库**：输出、配置都放在被 git 忽略的 var/map/（地图包）。
提取配置 extract.json 是对着具体图量出来的（视图在模型空间里的位置、图例和标题栏的范围、图框的大小），
格式和说明见 scripts/map/extract.example.json；换一张矿井图就要重新量、重新写。

输出（坐标是图纸坐标减去视图原点，保持 y 向上，保留 2 位小数）：
- layers：折线，按图层分组；fills：实心填充（巷道本体、风机等），按 ACI 颜色分组，带孔洞；
- labels：文字，kind 为 name（巷道/设施名）、flow（风量）、section（断面风速）、note；names：名称 → 标注编号；
- legend：图例（符号和文字，相对图例框左下角），前端自己画，不占地图；
- 图框、标题栏不输出。
几何只用于展示，不参与任何判断；监测数值、风量等以矿井接口为准，图上的风量标注是出图时的静态值。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from geometry import Fill, Label, Poly  # noqa: E402
from load_drawing import load  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

# 名称标注：含这些词的短文字当作巷道/设施名，用来做定位索引。换矿井图可能要调。
NAME_WORDS = ("巷", "面", "石门", "斜井", "平硐", "下山", "上山", "车场", "煤仓", "水仓", "风井", "硐室", "联络")
FLOW_RE = re.compile(r"^\d+(\.\d+)?m³/min$")
SECTION_RE = re.compile(r"^S=")

Box = tuple[float, float, float, float]  # x0, x1, y0, y1


def classify(text: str) -> str:
    if FLOW_RE.match(text):
        return "flow"
    if SECTION_RE.match(text):
        return "section"
    if len(text) <= 24 and any(w in text for w in NAME_WORDS) and not text.startswith("型号"):
        return "name"
    return "note"


def _bbox(pts: list[tuple[float, float]]) -> Box:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def _inside(x: float, y: float, box: Box) -> bool:
    return box[0] <= x <= box[1] and box[2] <= y <= box[3]


def _range_ok(v: float, rng: list[float | None] | None) -> bool:
    if not rng:
        return True
    lo, hi = rng
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"没有提取配置 {path}。参照 scripts/map/extract.example.json 对着这张图量出各范围后写一份。")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if "views" not in cfg:
        raise SystemExit(f"{path} 缺少 views")
    return cfg


def extract(drawing, cfg: dict, name: str) -> dict:  # noqa: ANN001
    sel_x, sel_y = cfg["select"].get("x"), cfg["select"].get("y")
    legend_box: Box | None = tuple(cfg["legend"]) if cfg.get("legend") else None  # type: ignore[assignment]
    exclude: list[Box] = [tuple(b) for b in cfg.get("exclude", [])]  # type: ignore[misc]
    fmin_w, fmin_h = cfg.get("frame_min_width", 1e18), cfg.get("frame_min_height", 1e18)
    layer_colors = drawing.layer_colors

    def selected(x: float, y: float) -> bool:
        return _range_ok(x, sel_x) and _range_ok(y, sel_y)

    dropped = {"frame": 0, "excluded": 0, "duplicate": 0, "stray_fill": 0}
    seen: set[tuple] = set()

    def route(cx: float, cy: float) -> str:
        """legend / drop / map"""
        if legend_box and _inside(cx, cy, legend_box):
            return "legend"
        if any(_inside(cx, cy, b) for b in exclude):
            dropped["excluded"] += 1
            return "drop"
        return "map"

    polys: list[Poly] = []
    legend_polys: list[Poly] = []
    for p in drawing.polys:
        if not selected(*p.pts[0]):
            continue
        x0, x1, y0, y1 = _bbox(p.pts)
        if x1 - x0 >= fmin_w or y1 - y0 >= fmin_h:
            dropped["frame"] += 1
            continue
        where = route((x0 + x1) / 2, (y0 + y1) / 2)
        if where == "drop":
            continue
        key = (p.layer, tuple((round(a, 2), round(b, 2)) for a, b in p.pts))
        if key in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(key)
        (legend_polys if where == "legend" else polys).append(p)

    fills: list[Fill] = []
    legend_fills: list[Fill] = []
    for f in drawing.fills:
        if not selected(*f.outer[0]):
            continue
        x0, x1, y0, y1 = _bbox(f.outer)
        if x1 - x0 >= fmin_w or y1 - y0 >= fmin_h:
            continue
        where = route((x0 + x1) / 2, (y0 + y1) / 2)
        if where == "drop":
            continue
        (legend_fills if where == "legend" else fills).append(f)

    labels: list[Label] = []
    legend_labels: list[Label] = []
    lseen: set[tuple] = set()
    for lb in drawing.labels:
        if not selected(lb.x, lb.y):
            continue
        where = route(lb.x, lb.y)
        if where == "drop":
            continue
        key = (lb.text, round(lb.x, 1), round(lb.y, 1))
        if key in lseen:
            continue
        lseen.add(key)
        (legend_labels if where == "legend" else labels).append(lb)

    # 保险：杂散的填充（图里别处的东西，选择范围没排除干净）不能把整张图的范围撑大。
    # 以折线和标注的包围盒为准，填充的中心落在它外面（放宽 5%）就丢掉并计数。
    bx = [a for p in polys for a, _ in p.pts] + [lb.x for lb in labels]
    by = [b for p in polys for _, b in p.pts] + [lb.y for lb in labels]
    mx, my = (max(bx) - min(bx)) * 0.05, (max(by) - min(by)) * 0.05
    core: Box = (min(bx) - mx, max(bx) + mx, min(by) - my, max(by) + my)
    kept = []
    for f in fills:
        x0, x1, y0, y1 = _bbox(f.outer)
        if _inside((x0 + x1) / 2, (y0 + y1) / 2, core):
            kept.append(f)
    dropped["stray_fill"] = len(fills) - len(kept)
    fills = kept

    allx = bx + [a for f in fills for a, _ in f.outer]
    ally = by + [b for f in fills for _, b in f.outer]
    ox, oy = min(allx), min(ally)

    def r(v: float) -> float:
        return round(v, 2)

    def flat(pts: list[tuple[float, float]], dx: float, dy: float) -> list[float]:
        out: list[float] = []
        for a, b in pts:
            out += [r(a - dx), r(b - dy)]
        return out

    def pack_layers(ps: list[Poly], dx: float, dy: float) -> dict:
        layers: dict[str, dict] = {}
        for p in ps:
            g = layers.setdefault(p.layer, {"aci": layer_colors.get(p.layer, 7), "lines": []})
            item: dict = {"p": flat(p.pts, dx, dy)}
            if p.closed:
                item["c"] = 1
            if p.color is not None:
                item["aci"] = p.color
            g["lines"].append(item)
        return layers

    def pack_fills(fs: list[Fill], dx: float, dy: float) -> dict:
        groups: dict[str, list] = {}
        for f in fs:
            item: dict = {"o": flat(f.outer, dx, dy)}
            if f.holes:
                item["h"] = [flat(h, dx, dy) for h in f.holes]
            groups.setdefault(str(f.aci) if f.aci is not None else "none", []).append(item)
        return groups

    out_labels = []
    names: dict[str, list[str]] = {}
    for i, lb in enumerate(sorted(labels, key=lambda t: (-t.y, t.x))):
        lid = f"L{i:04d}"
        kind = classify(lb.text)
        out_labels.append({"id": lid, "text": lb.text, "x": r(lb.x - ox), "y": r(lb.y - oy),
                           "h": r(lb.height), "rot": r(lb.rot), "kind": kind})
        if kind == "name":
            names.setdefault(lb.text, []).append(lid)

    legend = None
    if legend_box and (legend_polys or legend_labels or legend_fills):
        lx, ly = legend_box[0], legend_box[2]
        legend = {
            "size": [r(legend_box[1] - legend_box[0]), r(legend_box[3] - legend_box[2])],
            "layers": pack_layers(legend_polys, lx, ly),
            "fills": pack_fills(legend_fills, lx, ly),
            "labels": [{"text": lb.text, "x": r(lb.x - lx), "y": r(lb.y - ly), "h": r(lb.height), **({"m": 1} if lb.mtext else {})} for lb in legend_labels],
        }

    return {
        "version": 2,
        "view": name,
        "title": cfg.get("title", name),
        "source": cfg.get("source", ""),
        "origin": [r(ox), r(oy)],
        "bbox": [0, 0, r(max(allx) - ox), r(max(ally) - oy)],
        "layers": pack_layers(polys, ox, oy),
        "fills": pack_fills(fills, ox, oy),
        "labels": out_labels,
        "names": names,
        "legend": legend,
        "stats": {
            "polylines": len(polys), "fills": len(fills), "labels": len(out_labels), "names": len(names),
            "legend": None if legend is None else {"polylines": len(legend_polys), "fills": len(legend_fills), "labels": len(legend_labels)},
            "dropped": dropped,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dxf", default=str(ROOT / "var/map-work/out.dxf"))
    ap.add_argument("--config", default=str(ROOT / "var/map/extract.json"))
    ap.add_argument("--out", default=str(ROOT / "var/map"))
    ap.add_argument("--view", default=None, help="配置里的视图名；缺省取配置里的 default_view")
    ap.add_argument("--name", default="geometry.json", help="输出文件名（当前矿井的主底图固定叫 geometry.json）")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    view = args.view or cfg.get("default_view") or next(iter(cfg["views"]))
    if view not in cfg["views"]:
        raise SystemExit(f"配置里没有视图 {view}，有：{', '.join(cfg['views'])}")
    drawing = load(args.dxf)
    data = extract(drawing, cfg["views"][view], view)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / args.name
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path}: {path.stat().st_size / 1024:.0f} KB  {data['stats']}")


if __name__ == "__main__":
    main()
