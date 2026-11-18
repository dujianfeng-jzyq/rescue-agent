"""外部计算服务（路径规划、火灾推演）共用的占位坐标：我方网络没有坐标，按固定方法生成（docs/design-v6-route-service.md 附录 A.2）。

节点按编号排在单位圆上，整体缩放到每条巷道两端的直线距离都不超过巷道长度；每条巷道在中垂线上加一个中间节点，
使两段折线之和等于巷道长度。z 一律取 0（不引入编造的坡度）。图形本身没有意义，只保证长度。确定性：同一网络每次结果相同。
"""

from __future__ import annotations

import math

DIGITS = 3  # 坐标保留到毫米，保证输出确定


def rnd(v: float) -> float:
    """保留 DIGITS 位；把 -0.0 换成 0.0（JSON 里 Python 写 -0.0、JS 写 0，记录哈希会对不上）。"""
    return round(v, DIGITS) + 0.0


def layout(node_ids, roadways) -> dict[str, tuple[float, float]]:
    """节点按编号排在单位圆上，整体缩放使每条巷道两端的直线距离 d ≤ 巷道长度 L。"""
    ordered = sorted(node_ids)
    n = len(ordered)
    unit = {nid: (math.cos(2 * math.pi * k / n), math.sin(2 * math.pi * k / n)) for k, nid in enumerate(ordered)}
    scale = min(
        (
            r["length_m"] / math.dist(unit[r["from_node"]], unit[r["to_node"]])
            for r in roadways
            if unit[r["from_node"]] != unit[r["to_node"]]
        ),
        default=1.0,
    )
    return {nid: (x * scale, y * scale) for nid, (x, y) in unit.items()}


def mid_point(a, b, length) -> tuple[float, float]:
    """a、b 中垂线上的点 m，使 |am| + |mb| = length（在 a→b 方向的左侧）。"""
    d = math.dist(a, b)
    h = math.sqrt(max(0.0, (length / 2) ** 2 - (d / 2) ** 2))
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    if d == 0:
        return (mx, my + h)
    nx_, ny_ = -(b[1] - a[1]) / d, (b[0] - a[0]) / d
    return (mx + nx_ * h, my + ny_ * h)
