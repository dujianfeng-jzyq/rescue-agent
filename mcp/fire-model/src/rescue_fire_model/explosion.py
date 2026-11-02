"""火区混合气体爆炸危险性：温度补偿的爆炸三角形合成法。

坐标：横轴为可燃气体总浓度 F（%），纵轴为 O₂（%）。步骤：
1. 各可燃组分 i 的权重 wᵢ = Cᵢ / F；
2. 温度修正 rT = k × (T − 25)：Lᵢ 横坐标 × (1 − rT)，Uᵢ 横坐标 × (1 + rT)，纵坐标和 S 点不修正；
3. Sᵢ 按惰性气体组成在“N₂ 惰化临界点”和“CO₂ 惰化临界点”之间加权：
   权重为 CO₂ 实测值和 N₂ 权重（有可用实测值用实测值，否则用 3.73 × O₂ 当量）；
4. 混合气体顶点 L = Σ wᵢLᵢ，U、S 同理；状态点 P = (F, O₂)。

分区按“状态点怎样变化会进入三角形”判定（与技能原脚本不同，见下）：
- I  爆炸危险区：P 在三角形内或边上；
- III 增风危险区：P 与空气混合（沿 P → 空气点 (0, 20.93) 的线段）会穿过三角形，如增风、启封、密闭漏风；
- II 减风危险区：可燃气体继续积聚（沿 P → 纯可燃气体点 (100, 0) 的线段）会穿过三角形，如减风、停风；
- IV 相对安全区：以上两种变化都不会进入三角形。

原脚本用 L–S、U–S 两条直线的半平面划 II、III 区，三角形下方的低氧点（如 CH₄ 3%、O₂ 8%）也会被判成
“减风危险区”，与它自己画的分区图不一致；这里改为按混合方向精确求交。
“积聚”假定新增的是不含氧的可燃气体；若涌出的是含 N₂ 的采空区混合气，实际方向会偏下，结论偏保守。
TODO(专家): 审定分区判据和参数（explosion.yaml）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from rescue_contract.models import ExplosionRisk

CONSTANTS_PATH = Path(os.environ.get("RESCUE_EXPLOSION_CONSTANTS") or Path(__file__).with_name("explosion.yaml"))
EPS = 1e-9

Point = tuple[float, float]

ZONES: dict[str, tuple[str, ExplosionRisk, str]] = {
    "I": ("爆炸危险区", ExplosionRisk.EXPLOSIVE, "当前组成处于爆炸范围"),
    "II": ("减风危险区", ExplosionRisk.MAY_BECOME_EXPLOSIVE, "可燃气体继续积聚（如减风、停风）会进入爆炸范围"),
    "III": ("增风危险区", ExplosionRisk.MAY_BECOME_EXPLOSIVE, "混入空气（如增风、启封、密闭漏风）会进入爆炸范围"),
    "IV": ("相对安全区", ExplosionRisk.NOT_EXPLOSIVE, "当前组成下单纯混入空气或可燃气体积聚都不会进入爆炸范围；条件变化后须重新计算"),
}


@dataclass(frozen=True)
class GasVertices:
    lower: Point
    upper: Point
    critical_n2: Point
    critical_co2: Point


@dataclass(frozen=True)
class Constants:
    air_o2: float
    n2_per_o2: float
    reference_c: float
    coefficient_per_c: float
    min_c: float
    max_c: float
    composition_tolerance: float
    sample_window_h: float
    gases: dict[str, GasVertices]


def load_constants(path: Path = CONSTANTS_PATH) -> Constants:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    t = raw["temperature"]
    return Constants(
        air_o2=raw["air_o2_pct"],
        n2_per_o2=raw["n2_per_o2"],
        reference_c=t["reference_c"],
        coefficient_per_c=t["coefficient_per_c"],
        min_c=t["min_c"],
        max_c=t["max_c"],
        composition_tolerance=raw["composition_tolerance_pct"],
        sample_window_h=raw["sample_window_h"],
        gases={g: GasVertices(*(tuple(v[k]) for k in ("L", "U", "S_N2", "S_CO2"))) for g, v in raw["gases"].items()},
    )


@dataclass(frozen=True)
class Sample:
    """同一时刻、同一取样点的一组分析结果，浓度均为体积分数（%）。"""

    combustibles: dict[str, float]  # 键为 explosion.yaml 里的气体标识
    o2: float
    co2: float
    temperature_c: float
    n2: float | None = None  # 实测 N₂；None 表示没有可用实测值


# ── 几何 ──


def _cross(a: Point, b: Point, p: Point) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _inside(p: Point, tri: tuple[Point, Point, Point]) -> bool:
    """点在三角形内或边上（闭区域）。"""
    a, b, c = tri
    d = (_cross(a, b, p), _cross(b, c, p), _cross(c, a, p))
    return not (any(x < -EPS for x in d) and any(x > EPS for x in d))


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    return (
        abs(_cross(a, b, p)) <= EPS
        and min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
        and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS
    )


def _segments_meet(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    d1, d2 = _cross(q1, q2, p1), _cross(q1, q2, p2)
    d3, d4 = _cross(p1, p2, q1), _cross(p1, p2, q2)
    if ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)):
        return True
    return _on_segment(q1, q2, p1) or _on_segment(q1, q2, p2) or _on_segment(p1, p2, q1) or _on_segment(p1, p2, q2)


def _segment_hits_triangle(p: Point, q: Point, tri: tuple[Point, Point, Point]) -> bool:
    if _inside(p, tri) or _inside(q, tri):
        return True
    a, b, c = tri
    return any(_segments_meet(p, q, e1, e2) for e1, e2 in ((a, b), (b, c), (c, a)))


def classify(p: Point, tri: tuple[Point, Point, Point], air_o2: float) -> dict[str, Any]:
    """按混合方向判定分区，返回分区代码和两个方向各自是否会进入三角形。"""
    if _inside(p, tri):
        return {"zone": "I", "enters_if_air_added": True, "enters_if_combustible_accumulates": True}
    air = _segment_hits_triangle(p, (0.0, air_o2), tri)
    fuel = _segment_hits_triangle(p, (100.0, 0.0), tri)
    zone = "III" if air else "II" if fuel else "IV"
    return {"zone": zone, "enters_if_air_added": air, "enters_if_combustible_accumulates": fuel}


# ── 合成 ──


def _blend(points: list[tuple[float, Point]]) -> Point:
    return (sum(w * pt[0] for w, pt in points), sum(w * pt[1] for w, pt in points))


def _pt(p: Point) -> dict[str, float]:
    return {"combustible_pct": round(p[0], 4), "o2_pct": round(p[1], 4)}


def analyze(s: Sample, c: Constants) -> dict[str, Any]:
    """计算爆炸三角形和分区。输入不完整或超出适用范围时抛 ValueError（不估算、不补值）。"""
    for name, v in (("O2", s.o2), ("CO2", s.co2), ("N2", s.n2), *s.combustibles.items()):
        if v is not None and not 0 <= v <= 100:
            raise ValueError(f"{name} 浓度 {v}% 超出 0–100% 范围")
    unknown = sorted(set(s.combustibles) - set(c.gases))
    if unknown:
        raise ValueError(f"没有这些可燃气体的爆炸三角形顶点参数：{'、'.join(unknown)}；可用：{'、'.join(c.gases)}")
    if not c.min_c <= s.temperature_c <= c.max_c:
        raise ValueError(f"温度 {s.temperature_c}℃ 超出方法适用范围 {c.min_c}–{c.max_c}℃，不能计算")
    gases = {g: v for g, v in s.combustibles.items() if v > 0}
    total = sum(gases.values())
    if total <= 0:
        raise ValueError("未检出任何可燃气体，无法合成混合气体的爆炸三角形")
    limit = 100 + c.composition_tolerance
    known = total + s.o2 + s.co2
    if known > limit:
        raise ValueError(f"可燃气体、O2、CO2 之和 {known:.2f}% 超过 100%，读数可能有误，不能计算")

    # N2 只决定 S 点在两个临界点之间的位置。实测值与其他组分加起来超过 100% 时不可信，
    # 改用 O2 当量（N2 权重更小，S 点偏向 CO2 临界点，三角形更大，结论偏保守）。
    n2_dropped = s.n2 is not None and known + s.n2 > limit
    if s.n2 is not None and not n2_dropped:
        n2_weight, n2_source = s.n2, "measured"
    else:
        n2_weight, n2_source = c.n2_per_o2 * s.o2, "o2_equivalent"
    inert_total = s.co2 + n2_weight
    if inert_total <= 0:
        raise ValueError("CO2 和 N2 权重都为 0，无法确定临界点（S 点），不能计算")

    rt = c.coefficient_per_c * (s.temperature_c - c.reference_c)
    weights = {g: v / total for g, v in gases.items()}
    lows, ups, crits = [], [], []
    for g, w in weights.items():
        v = c.gases[g]
        lows.append((w, (v.lower[0] * (1 - rt), v.lower[1])))
        ups.append((w, (v.upper[0] * (1 + rt), v.upper[1])))
        crit = tuple((v.critical_co2[k] * s.co2 + v.critical_n2[k] * n2_weight) / inert_total for k in (0, 1))
        crits.append((w, crit))
    tri = (_blend(lows), _blend(ups), _blend(crits))
    if abs(_cross(*tri)) <= 1e-12:
        raise ValueError("合成的三角形退化，检查参数和输入")
    p = (total, s.o2)
    cls = classify(p, tri, c.air_o2)
    name, risk, meaning = ZONES[cls["zone"]]

    warnings = ["顶点、温度修正系数等参数为开发占位值，待专家审定；结果用于风险研判，不替代现场检测和指挥决策"]
    if n2_dropped:
        warnings.append(f"N2 实测值 {s.n2}% 与其他组分之和 {known + s.n2:.2f}% 超过 100%，不采用，改用 O2 当量")
    elif n2_source == "o2_equivalent":
        warnings.append(f"没有可用的 N2 实测值，S 点按 N2 当量 = {c.n2_per_o2} × O2 合成（不是实测 N2），注氮惰化时结论偏保守")
    if cls["zone"] == "IV":
        warnings.append("IV 区只说明当前组成；气体组成或风流变化后须重新计算")
    return {
        "zone": cls["zone"],
        "zone_name": name,
        "explosion_risk": risk.value,
        "meaning": meaning,
        "enters_if_air_added": cls["enters_if_air_added"],
        "enters_if_combustible_accumulates": cls["enters_if_combustible_accumulates"],
        "state_point": _pt(p),
        "triangle": {"lower": _pt(tri[0]), "upper": _pt(tri[1]), "critical": _pt(tri[2])},
        "combustibles_pct": {g: round(v, 6) for g, v in gases.items()},
        "combustible_total_pct": round(total, 6),
        "combustible_weights": {g: round(w, 4) for g, w in weights.items()},
        "temperature_c": s.temperature_c,
        "temperature_factor": round(rt, 6),
        "inert": {"co2_pct": s.co2, "n2_weight": round(n2_weight, 4), "n2_source": n2_source},
        "warnings": warnings,
        "method": "温度补偿爆炸三角形合成法（多组分）；分区按混入空气/可燃气体积聚两个方向是否进入三角形判定",
    }
