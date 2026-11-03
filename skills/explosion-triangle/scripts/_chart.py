"""爆炸三角形风险图：把 explosion.analyze 的同一次结果画成自包含 SVG，并和 JSON 一起存盘。

图只是结果的另一种表达，不做第二套判定：分区色块按 explosion.classify 的同一几何定义构造
（混入空气的“影子”= 三角形与它以空气点为中心放大后的凸包；可燃气体积聚同理以 (100, 0) 为中心），
测试会在网格上逐点核对色块与判定一致。

由 mcp/fire-model 的 chart.py 迁移（去掉存盘和下载链接：图随结果记录保存，按记录编号下载）。
"""

from __future__ import annotations

from html import escape
from typing import Any

from _explosion import ZONES, Point

FAR = 200.0  # 放大倍数，足够让“影子”伸出图框
ZONE_FILL = {"IV": "#CDEFD8", "II": "#FDD9A8", "III": "#FBEFA0", "I": "#F6A9A9"}
ZONE_INK = {"IV": "#11734B", "II": "#B4540A", "III": "#8A6D00", "I": "#B42318"}
RISK_NAME = {"explosive": "处于爆炸范围", "may_become_explosive": "可能转为爆炸危险", "not_explosive": "当前不会爆炸"}
TITLE = "火区混合气体爆炸三角形"


# ── 几何 ──


def _hull(points: list[Point]) -> list[Point]:
    """凸包（逆时针）。"""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o: Point, a: Point, b: Point) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[Point] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _clip(subject: list[Point], clip: list[Point]) -> list[Point]:
    """Sutherland–Hodgman：用逆时针凸多边形 clip 裁剪 subject。"""

    def inside(p: Point, a: Point, b: Point) -> bool:
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= 0

    def meet(p: Point, q: Point, a: Point, b: Point) -> Point:
        x1, y1, x2, y2, x3, y3, x4, y4 = *p, *q, *a, *b
        den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))

    out = subject
    for i, a in enumerate(clip):
        b = clip[(i + 1) % len(clip)]
        src, out = out, []
        for j, p in enumerate(src):
            q = src[(j + 1) % len(src)]
            if inside(q, a, b):
                if not inside(p, a, b):
                    out.append(meet(p, q, a, b))
                out.append(q)
            elif inside(p, a, b):
                out.append(meet(p, q, a, b))
        if not out:
            break
    return out


def physical_region(air_o2: float, xmax: float) -> list[Point]:
    """空气线以下的可能组成（逆时针）。"""
    pts = [(0.0, 0.0), (xmax, 0.0), (xmax, max(0.0, air_o2 * (1 - xmax / 100))), (0.0, air_o2)]
    return [q for i, q in enumerate(pts) if q != pts[i - 1]]  # xmax = 100 时右侧两点重合


def zone_polygons(tri: tuple[Point, Point, Point], air_o2: float, xmax: float) -> dict[str, list[Point]]:
    """各分区色块，按 IV → II → III → I 的顺序叠画，后画的覆盖先画的（与 classify 的优先级一致）。"""
    phys = physical_region(air_o2, xmax)

    def shadow(center: Point) -> list[Point]:
        far = [(center[0] + (p[0] - center[0]) * FAR, center[1] + (p[1] - center[1]) * FAR) for p in tri]
        return _clip(_hull([*tri, *far]), phys)

    return {"IV": phys, "II": shadow((100.0, 0.0)), "III": shadow((0.0, air_o2)), "I": _clip(_hull(list(tri)), phys)}


def drawn_zone(p: Point, polygons: dict[str, list[Point]]) -> str | None:
    """点落在哪个色块上（取最上层），供测试核对图与判定一致。"""

    def contains(poly: list[Point]) -> bool:
        if len(poly) < 3:
            return False
        signs = [(b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) for a, b in zip(poly, poly[1:] + poly[:1], strict=True)]
        return all(s >= 0 for s in signs) or all(s <= 0 for s in signs)

    hit = None
    for zone in ("IV", "II", "III", "I"):
        if contains(polygons[zone]):
            hit = zone
    return hit


# ── 绘图 ──


def _nice_max(v: float) -> float:
    for step in (10, 15, 20, 25, 30, 40, 50, 60, 80, 100):
        if v <= step:
            return float(step)
    return 100.0


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") if abs(v) >= 1 else f"{v:.4g}"


def _wrap(text: str, width: int) -> list[str]:
    return [text[i : i + width] for i in range(0, len(text), width)] or [""]


def _xy(r: dict[str, float]) -> Point:
    return (r["combustible_pct"], r["o2_pct"])


def render_svg(result: dict[str, Any], air_o2: float, subtitle: str) -> str:
    """把 explosion.analyze（及工具补充的 sample）结果画成 SVG 文本。"""
    tri = (_xy(result["triangle"]["lower"]), _xy(result["triangle"]["upper"]), _xy(result["triangle"]["critical"]))
    p = _xy(result["state_point"])
    xmax = _nice_max(max(*(v[0] for v in tri), p[0]) * 1.15)
    ymax = 22.0 if p[1] <= 21.5 else float(int(p[1]) + 2)
    W, H = 1000, 640
    left, top, pw, ph = 70.0, 92.0, 560.0, 460.0
    panel_x = left + pw + 30

    def sx(x: float) -> float:
        return left + x / xmax * pw

    def sy(y: float) -> float:
        return top + ph - y / ymax * ph

    def poly(points: list[Point]) -> str:
        return " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in points)

    zone = result["zone"]
    ink = ZONE_INK[zone]
    polys = zone_polygons(tri, air_o2, xmax)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{TITLE}">',
        "<style>text{font-family:'Noto Sans CJK SC','Source Han Sans SC','Microsoft YaHei','PingFang SC',sans-serif;fill:#1F2937}"
        ".t{font-size:12px;fill:#5B6472}.lb{font-size:13px;font-weight:600}.zn{font-size:14px;font-weight:700}.m{font-size:13px}</style>",
        f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>',
        f'<text x="{left}" y="38" font-size="22" font-weight="700">{TITLE}</text>',
        f'<text x="{left}" y="64" class="t">{escape(subtitle)}</text>',
        f'<rect x="{left}" y="{top}" width="{pw}" height="{ph}" fill="#EEF1F5"/>',  # 空气线以上：不可能的组成
    ]
    for z in ("IV", "II", "III", "I"):
        if len(polys[z]) >= 3:
            out.append(f'<polygon points="{poly(polys[z])}" fill="{ZONE_FILL[z]}"/>')
    # 网格与刻度
    xstep = xmax / 10
    for i in range(11):
        x = sx(i * xstep)
        out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + ph}" stroke="#FFFFFF" stroke-opacity="0.6"/>')
        out.append(f'<text x="{x:.1f}" y="{top + ph + 18}" class="t" text-anchor="middle">{_fmt(i * xstep)}</text>')
    for yv in range(0, int(ymax) + 1, 2):
        y = sy(yv)
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + pw}" y2="{y:.1f}" stroke="#FFFFFF" stroke-opacity="0.6"/>')
        out.append(f'<text x="{left - 8}" y="{y + 4:.1f}" class="t" text-anchor="end">{yv}</text>')
    air_end = (xmax, max(0.0, air_o2 * (1 - xmax / 100)))
    out += [
        f'<line x1="{sx(0)}" y1="{sy(air_o2):.1f}" x2="{sx(air_end[0]):.1f}" y2="{sy(air_end[1]):.1f}" stroke="#475467" stroke-width="1.5" stroke-dasharray="6 4"/>',
        f'<text x="{sx(0) + 6}" y="{sy(air_o2) - 6:.1f}" class="t">空气线</text>',
        f'<polygon points="{poly(list(tri))}" fill="none" stroke="#7A271A" stroke-width="2"/>',
        f'<rect x="{left}" y="{top}" width="{pw}" height="{ph}" fill="none" stroke="#98A2B3"/>',
        f'<text x="{left + pw / 2}" y="{top + ph + 42}" class="m" text-anchor="middle">可燃气体总浓度（%）</text>',
        f'<text x="20" y="{top + ph / 2}" class="m" text-anchor="middle" transform="rotate(-90 20 {top + ph / 2})">O₂（%）</text>',
    ]
    # 分区名：放在该区里离色块重心最近的网格点上（保证文字落在对应区内）
    for z in ("IV", "II", "III", "I"):
        pts = [q for q in ((xmax * (i + 0.5) / 40, ymax * (j + 0.5) / 30) for i in range(40) for j in range(30)) if drawn_zone(q, polys) == z]
        if not pts or len(polys[z]) < 3:
            continue
        cx = sum(x for x, _ in polys[z]) / len(polys[z])
        cy = sum(y for _, y in polys[z]) / len(polys[z])
        q = min(pts, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)
        out.append(f'<text x="{sx(q[0]):.1f}" y="{sy(q[1]):.1f}" class="zn" fill="{ZONE_INK[z]}" text-anchor="middle">{z} {ZONES[z][0]}</text>')
    for name, v, dy in (("L", tri[0], -10), ("U", tri[1], -10), ("S", tri[2], 18)):
        out.append(f'<circle cx="{sx(v[0]):.1f}" cy="{sy(v[1]):.1f}" r="4" fill="#111827"/>')
        out.append(f'<text x="{sx(v[0]) + 7:.1f}" y="{sy(v[1]) + dy:.1f}" class="lb">{name}({_fmt(v[0])}, {_fmt(v[1])})</text>')
    px, py = sx(min(p[0], xmax)), sy(min(p[1], ymax))
    out += [
        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="8" fill="{ink}" stroke="#FFFFFF" stroke-width="2.5"/>',
        f'<text x="{px + 12:.1f}" y="{py + 5:.1f}" class="lb" fill="{ink}">P({_fmt(p[0])}, {_fmt(p[1])})</text>',
    ]
    # 右侧结论卡片
    y = top
    out += [
        f'<rect x="{panel_x}" y="{y}" width="{W - panel_x - 24}" height="88" rx="10" fill="{ZONE_FILL[zone]}"/>',
        f'<text x="{panel_x + 16}" y="{y + 30}" class="m" fill="{ink}">判定结果</text>',
        f'<text x="{panel_x + 16}" y="{y + 64}" font-size="24" font-weight="700" fill="{ink}">{zone} 区 · {ZONES[zone][0]}</text>',
    ]
    y += 112
    lines = [f"爆炸危险性：{RISK_NAME[result['explosion_risk']]}", *_wrap(result["meaning"], 19), ""]
    # 组分按项换行，不把数字拆开；低于 0.1% 的痕量气体用 ppm 显示
    items = [f"{g} {_fmt(v * 10_000)} ppm" if v < 0.1 else f"{g} {_fmt(v)}%" for g, v in result["combustibles_pct"].items()]
    lines += [
        f"可燃气体合计：{_fmt(result['combustible_total_pct'])}%",
        *(("组分：" if i == 0 else "　　　") + "　".join(items[i : i + 2]) for i in range(0, len(items), 2)),
        f"O₂：{_fmt(p[1])}%　CO₂：{_fmt(result['inert']['co2_pct'])}%",
        f"N₂ 权重：{_fmt(result['inert']['n2_weight'])}（{'实测' if result['inert']['n2_source'] == 'measured' else 'O₂ 当量'}）",
        f"温度：{_fmt(result['temperature_c'])} ℃　修正系数 rT：{result['temperature_factor']:.6f}",
    ]
    for line in lines:
        out.append(f'<text x="{panel_x + 4}" y="{y}" class="m">{escape(line)}</text>')
        y += 22
    foot = "参数为开发占位值，待专家审定；结论以计算结果 JSON 的 zone 为准，不替代现场检测和指挥决策。"
    out.append(f'<text x="{left}" y="{H - 16}" class="t">{escape(foot)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"
