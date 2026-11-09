"""读 DXF，得到模型空间的折线和文字（块插入展开一层）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dxfscan import Entity, iter_entities
from geometry import Fill, Label, Poly, entity_labels, entity_polys, hatch_fills, transform


@dataclass
class Drawing:
    polys: list[Poly] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    layer_colors: dict[str, int] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)


def load(path: str | Path) -> Drawing:
    d = Drawing()
    block_base: dict[str, tuple[float, float]] = {}
    block_polys: dict[str, list[Poly]] = {}
    block_labels: dict[str, list[Label]] = {}
    block_fills: dict[str, list[Fill]] = {}
    model: list[Entity] = []
    inserts_in_blocks = 0

    for e in iter_entities(path):
        if e.section == "TABLES" and e.type == "LAYER":
            name = e.first(2)
            try:
                c = abs(int(e.first(62, "7") or 7))
            except ValueError:
                c = 7
            if name:
                d.layer_colors[name] = c
        elif e.section == "BLOCKS":
            if e.type == "BLOCK" and e.block:
                block_base[e.block] = (e.num(10), e.num(20))
                block_polys.setdefault(e.block, [])
                block_labels.setdefault(e.block, [])
                block_fills.setdefault(e.block, [])
            elif e.block and e.type != "ENDBLK":
                if e.type == "INSERT":
                    inserts_in_blocks += 1  # 嵌套块不展开
                    continue
                block_polys.setdefault(e.block, []).extend(entity_polys(e))
                block_labels.setdefault(e.block, []).extend(entity_labels(e))
                block_fills.setdefault(e.block, []).extend(hatch_fills(e))
        elif e.section == "ENTITIES":
            model.append(e)

    missing = 0
    for e in model:
        if e.type == "INSERT":
            name = e.first(2) or ""
            if name not in block_polys:
                missing += 1
                continue
            ox, oy = e.num(10), e.num(20)
            sx, sy = e.num(41, 1.0) or 1.0, e.num(42, 1.0) or 1.0
            rot = e.num(50)
            bx, by = block_base.get(name, (0.0, 0.0))
            for p in block_polys[name]:
                d.polys.append(Poly(transform(p.pts, ox, oy, sx, sy, rot, bx, by), p.layer, p.closed, p.color))
            for f in block_fills.get(name, []):
                outer = transform(f.outer, ox, oy, sx, sy, rot, bx, by)
                holes = [transform(h, ox, oy, sx, sy, rot, bx, by) for h in f.holes]
                d.fills.append(Fill(outer, holes, f.layer, f.aci))
            for lb in block_labels[name]:
                (x, y), = transform([(lb.x, lb.y)], ox, oy, sx, sy, rot, bx, by)
                d.labels.append(Label(lb.text, x, y, lb.height * abs(sy), lb.rot + rot, lb.layer, lb.mtext))
        else:
            d.polys.extend(entity_polys(e))
            d.labels.extend(entity_labels(e))
            d.fills.extend(hatch_fills(e))
    d.stats = {"model_entities": len(model), "blocks": len(block_polys), "missing_blocks": missing,
               "nested_inserts_skipped": inserts_in_blocks, "polys": len(d.polys), "fills": len(d.fills), "labels": len(d.labels)}
    return d
