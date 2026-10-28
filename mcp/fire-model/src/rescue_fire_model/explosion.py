"""甲烷—空气混合气体爆炸危险性判定（爆炸三角形 / Coward 图）。

坐标：横轴 CH₄%，纵轴 O₂%。三角形顶点取文献常用值，可在配置中调整：
- 爆炸下限点 (5.0, 19.88)、爆炸上限点 (15.0, 17.79)：位于空气混合线 O₂ = 20.93 × (1 − CH₄/100) 上；
- 鼻点 (6.0, 12.0)：低于该氧浓度时任何甲烷浓度都不会爆炸。

分区（用于“混入空气后是否可能爆炸”的判断）：
- explosive：落在三角形内；
- may_become_explosive：不在三角形内，但与空气混合（沿着指向空气点 (0, 20.93) 的直线）时会穿过三角形；
- not_explosive：其他情况。

只考虑甲烷。火区气体中还有 CO、H₂、乙烯等可燃组分时，应按混合气体（Le Chatelier 法则）计算。
TODO(算法): 支持多组分混合可燃气体；TODO(专家): 确认顶点取值。
"""

from __future__ import annotations

from dataclasses import dataclass

from rescue_contract.models import ExplosionRisk

AIR_O2 = 20.93


@dataclass(frozen=True)
class Triangle:
    lel: tuple[float, float] = (5.0, AIR_O2 * (1 - 5.0 / 100))
    uel: tuple[float, float] = (15.0, AIR_O2 * (1 - 15.0 / 100))
    nose: tuple[float, float] = (6.0, 12.0)


def _sign(p, a, b) -> float:
    return (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])


def _inside(p: tuple[float, float], t: Triangle) -> bool:
    d1, d2, d3 = _sign(p, t.lel, t.uel), _sign(p, t.uel, t.nose), _sign(p, t.nose, t.lel)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


DEFAULT_TRIANGLE = Triangle()


def classify(ch4_pct: float, o2_pct: float, t: Triangle = DEFAULT_TRIANGLE) -> ExplosionRisk:
    if not (0 <= ch4_pct <= 100 and 0 <= o2_pct <= AIR_O2 + 0.5):
        raise ValueError("浓度超出物理范围")
    p = (ch4_pct, o2_pct)
    if _inside(p, t):
        return ExplosionRisk.EXPLOSIVE
    # 与空气混合：沿 p → 空气点 (0, 20.93) 的线段取样
    air = (0.0, AIR_O2)
    for i in range(1, 200):
        k = i / 200
        q = (p[0] + (air[0] - p[0]) * k, p[1] + (air[1] - p[1]) * k)
        if _inside(q, t):
            return ExplosionRisk.MAY_BECOME_EXPLOSIVE
    return ExplosionRisk.NOT_EXPLOSIVE
