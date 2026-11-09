"""把 DXF 实体转成二维折线和文字（只用标准库）。

输出的几何都是 (x, y) 折线；圆弧、圆、样条用折线近似；块插入展开一层（符号）。
HATCH、DIMENSION 暂不处理（填充和尺寸线对通风网络图没有信息量）。
"""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass

from dxfscan import Entity, clean_text

Pt = tuple[float, float]


@dataclass
class Poly:
    pts: list[Pt]
    layer: str
    closed: bool = False
    color: int | None = None  # 实体自己的 ACI 颜色（62），None 表示随层


@dataclass
class Fill:
    """实心填充：外边界 + 若干孔洞（HATCH 的内部岛）。"""

    outer: list[Pt]
    holes: list[list[Pt]]
    layer: str
    aci: int | None  # 实体自己的 ACI 颜色（62）；None 表示随层


@dataclass
class Label:
    text: str
    x: float
    y: float
    height: float
    rot: float  # 度
    layer: str
    mtext: bool


def _floats(e: Entity, code: int) -> list[float]:
    out = []
    for v in e.all(code):
        with contextlib.suppress(ValueError):
            out.append(float(v))
    return out


def _aci(e: Entity) -> int | None:
    v = e.first(62)
    try:
        c = int(v) if v is not None else None
    except ValueError:
        return None
    return c if c not in (None, 0, 256) else None


def _arc_pts(cx: float, cy: float, r: float, a0: float, a1: float, seg_deg: float = 6.0) -> list[Pt]:
    while a1 <= a0:
        a1 += 360.0
    n = max(2, int(math.ceil((a1 - a0) / seg_deg)) + 1)
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / (n - 1))),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / (n - 1)))) for i in range(n)]


def _bulge_pts(p: Pt, q: Pt, bulge: float) -> list[Pt]:
    """LWPOLYLINE 的凸度段：p→q 的圆弧，不含 p。bulge>0 逆时针，<0 顺时针。"""
    if abs(bulge) < 1e-9:
        return [q]
    dx, dy = q[0] - p[0], q[1] - p[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-9:
        return [q]
    sign = 1.0 if bulge > 0 else -1.0
    theta = 4 * math.atan(abs(bulge))  # 圆心角，0..2π
    r = chord / (2 * math.sin(theta / 2))
    d = r * math.cos(theta / 2)  # 圆心到弦中点的距离，大于半圆时为负
    nx, ny = -dy / chord, dx / chord  # 弦方向的左法线
    cx = (p[0] + q[0]) / 2 + sign * nx * d
    cy = (p[1] + q[1]) / 2 + sign * ny * d
    a0 = math.atan2(p[1] - cy, p[0] - cx)
    steps = max(2, int(math.degrees(theta) / 6) + 1)
    return [(cx + r * math.cos(a0 + sign * theta * i / steps), cy + r * math.sin(a0 + sign * theta * i / steps))
            for i in range(1, steps + 1)]


def entity_polys(e: Entity) -> list[Poly]:
    layer = e.first(8, "0") or "0"
    col = _aci(e)
    t = e.type
    if t == "LINE":
        return [Poly([(e.num(10), e.num(20)), (e.num(11), e.num(21))], layer, False, col)]
    if t == "LWPOLYLINE":
        xs, ys = _floats(e, 10), _floats(e, 20)
        n = min(len(xs), len(ys))
        if n < 2:
            return []
        bulges: dict[int, float] = {}
        # 凸度（42）与顶点按出现顺序对应：在标签流里数它前面已出现的顶点数
        k = -1
        for c, v in e.tags:
            if c == 10:
                k += 1
            elif c == 42 and k >= 0:
                with contextlib.suppress(ValueError):
                    bulges[k] = float(v)
        closed = bool(int(float(e.first(70, "0") or 0)) & 1)
        pts: list[Pt] = [(xs[0], ys[0])]
        seq = list(range(n)) + ([0] if closed else [])
        for a, b in zip(seq, seq[1:], strict=False):
            pts.extend(_bulge_pts((xs[a], ys[a]), (xs[b], ys[b]), bulges.get(a, 0.0)))
        return [Poly(pts, layer, closed, col)]
    if t == "ARC":
        return [Poly(_arc_pts(e.num(10), e.num(20), e.num(40), e.num(50), e.num(51)), layer, False, col)]
    if t == "CIRCLE":
        return [Poly(_arc_pts(e.num(10), e.num(20), e.num(40), 0, 360), layer, True, col)]
    if t == "LEADER":
        xs, ys = _floats(e, 10), _floats(e, 20)
        n = min(len(xs), len(ys))
        return [Poly(list(zip(xs[:n], ys[:n], strict=True)), layer, False, col)] if n >= 2 else []
    if t == "SPLINE":
        xs, ys = _floats(e, 11) or _floats(e, 10), _floats(e, 21) or _floats(e, 20)
        n = min(len(xs), len(ys))
        return [Poly(list(zip(xs[:n], ys[:n], strict=True)), layer, False, col)] if n >= 2 else []
    return []


def entity_labels(e: Entity) -> list[Label]:
    if e.type not in ("TEXT", "MTEXT"):
        return []
    raw = e.first(1)
    if not raw:
        return []
    txt = clean_text(raw)
    if not txt:
        return []
    try:
        rot = float(e.first(50, "0") or 0)
    except ValueError:
        rot = 0.0
    return [Label(txt, e.num(10), e.num(20), e.num(40, 1.0), rot, e.first(8, "0") or "0", e.type == "MTEXT")]


def transform(pts: list[Pt], ox: float, oy: float, sx: float, sy: float, rot_deg: float, bx: float = 0, by: float = 0) -> list[Pt]:
    c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    out = []
    for x, y in pts:
        x, y = (x - bx) * sx, (y - by) * sy
        out.append((ox + x * c - y * s, oy + x * s + y * c))
    return out


def _ellipse_pts(cx: float, cy: float, mx: float, my: float, ratio: float, a0: float, a1: float, ccw: bool) -> list[Pt]:
    """椭圆弧（HATCH 边类型 3，角度为度；长轴向量 (mx, my)，短轴 = 长轴逆时针转 90° 乘 ratio）。
    圆弧是 ratio=1、长轴 = (r, 0) 的特例。ccw=False 时从起角顺时针走到终角。"""
    if ccw:
        while a1 <= a0:
            a1 += 360.0
    else:
        while a1 >= a0:
            a1 -= 360.0
    n = max(4, int(math.ceil(abs(a1 - a0) / 8.0)) + 1)
    out: list[Pt] = []
    for i in range(n):
        t = math.radians(a0 + (a1 - a0) * i / (n - 1))
        c, sn = math.cos(t), math.sin(t)
        out.append((cx + c * mx - sn * ratio * my, cy + c * my + sn * ratio * mx))
    return out


def hatch_fills(e: Entity) -> list[Fill]:
    """解析实心 HATCH（组码 70=1）的边界路径。图案填充（GRAVEL 等）不处理。

    路径按出现顺序：带“外边界”标志（92 的第 0 位）的是外轮廓，其后不带该标志的是它的孔洞。
    样条边（72=4）整条路径放弃，不画一半。
    """
    if e.type != "HATCH" or (e.first(70) or "").strip() != "1":
        return []
    tags = e.tags
    i = next((k for k, (c, _) in enumerate(tags) if c == 91), None)
    if i is None:
        return []
    npaths = int(float(tags[i][1]))
    i += 1
    layer = e.first(8, "0") or "0"
    aci = _aci(e)

    def num(k: int) -> float:
        try:
            return float(tags[k][1])
        except (ValueError, IndexError):
            return 0.0

    paths: list[tuple[bool, list[Pt]]] = []
    for _ in range(npaths):
        while i < len(tags) and tags[i][0] != 92:
            i += 1
        if i >= len(tags):
            break
        flag = int(num(i))
        i += 1
        pts: list[Pt] = []
        ok = True
        if flag & 2:  # 折线路径
            has_bulge = int(num(i)) if tags[i][0] == 72 else 0
            i += 1
            if tags[i][0] == 73:
                i += 1
            nv = int(num(i)) if tags[i][0] == 93 else 0
            i += 1
            verts: list[tuple[float, float, float]] = []
            for _v in range(nv):
                x, y, b = num(i), num(i + 1), 0.0
                i += 2
                if has_bulge and i < len(tags) and tags[i][0] == 42:
                    b = num(i)
                    i += 1
                verts.append((x, y, b))
            if verts:
                pts.append((verts[0][0], verts[0][1]))
            for k in range(len(verts)):
                a, b2 = verts[k], verts[(k + 1) % len(verts)]
                pts.extend(_bulge_pts((a[0], a[1]), (b2[0], b2[1]), a[2]))
        else:
            ne = int(num(i)) if tags[i][0] == 93 else 0
            i += 1
            for _k in range(ne):
                et = int(num(i))
                i += 1
                if et == 1:  # 直线
                    p0, p1 = (num(i), num(i + 1)), (num(i + 2), num(i + 3))
                    i += 4
                    pts.extend([p0, p1] if not pts else [p1])
                elif et == 2:  # 圆弧
                    cx, cy, r, a0, a1 = num(i), num(i + 1), num(i + 2), num(i + 3), num(i + 4)
                    ccw = int(num(i + 5)) != 0
                    i += 6
                    pts.extend(_ellipse_pts(cx, cy, r, 0.0, 1.0, a0, a1, ccw))
                elif et == 3:  # 椭圆弧
                    cx, cy, mx, my, ratio, a0, a1 = (num(i + j) for j in range(7))
                    ccw = int(num(i + 7)) != 0
                    i += 8
                    pts.extend(_ellipse_pts(cx, cy, mx, my, ratio, a0, a1, ccw))
                else:  # 样条等：放弃这条路径
                    ok = False
                    break
        if ok and len(pts) >= 3:
            paths.append((bool(flag & 1), pts))
    fills: list[Fill] = []
    for external, pts in paths:
        if external or not fills:
            fills.append(Fill(pts, [], layer, aci))
        else:
            fills[-1].holes.append(pts)
    return fills
