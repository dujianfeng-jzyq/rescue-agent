"""数据质量检测与速率：在研判前识别卡值、尖峰、漂移等故障读数。

读数是证据里的字典：{sensor_id, measurand, ts, value, quality}，ts 为矿井时间（ISO 8601，不带时区）。
由 mcp/fire-model 的 quality.py、warning.slope_per_hour 迁移，判据不变。

本文件在 fire-assessment、explosion-triangle 两个技能里各有一份，内容必须相同
（mcp/contract/tests/test_skill_scripts.py 检查）。改动时两份一起改。

TODO(算法): 现在是简单规则，阶段 2 用历史数据调整阈值，必要时引入统计模型。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

Reading = dict[str, Any]
_EPOCH = datetime(1970, 1, 1)


def ts(r: Reading) -> datetime:
    return datetime.fromisoformat(r["ts"])


def hours(t: datetime) -> float:
    """矿井时间换算成小时数（按 UTC 历元，与时区设置无关，脚本输出才是确定的）。"""
    return (t - _EPOCH).total_seconds() / 3600


@dataclass
class QualityReport:
    usable: list[Reading]
    flags: list[str] = field(default_factory=list)  # 给人看的说明


def _ok(r: Reading) -> bool:
    return r.get("quality", "ok") == "ok" and r.get("value") is not None


def _valid(rs: list[Reading]) -> list[Reading]:
    return [r for r in rs if _ok(r)]


def detect_stuck(rs: list[Reading], min_points: int = 6) -> bool:
    """最近 min_points 个点完全不变，视为卡值。"""
    vals = [r["value"] for r in _valid(rs)][-min_points:]
    return len(vals) >= min_points and max(vals) - min(vals) < 1e-9


def spike_indices(rs: list[Reading], factor: float = 4.0, floor: float = 5.0) -> set[int]:
    """单点超过前后邻居中位数的 factor 倍（且超过 floor），视为尖峰。"""
    vals = [r.get("value") for r in rs]
    out = set()
    for i, v in enumerate(vals):
        if v is None:
            continue
        neigh = [x for x in vals[max(0, i - 4) : i] + vals[i + 1 : i + 5] if x is not None]
        if len(neigh) >= 3:
            med = statistics.median(neigh)
            if v > floor and v > factor * max(med, 0.1):
                out.add(i)
    return out


def _constant_zero(rs: list[Reading], min_points: int = 6) -> bool:
    vals = [r["value"] for r in _valid(rs)][-min_points:]
    return len(vals) >= min_points and max(abs(v) for v in vals) < 1e-9


def check_series(name: str, rs: list[Reading], sibling: list[Reading] | None = None) -> QualityReport:
    """检查单个测点单个参数的序列（按时间排好序）。

    `sibling` 是同一测点上另一个持续变化的参数（如束管的 CO）。乙烯、乙炔等标志气体在出现之前
    真实读数就是 0：若序列恒为 0 而同测点的 sibling 仍在变化，说明测点在工作，按“未检出”保留，
    不判卡值。不给 sibling 或 sibling 也不变时，恒 0 仍按卡值排除（宁可判缺数，不把卡在 0 读成未检出）。
    """
    flags = []
    missing = sum(1 for r in rs if r.get("quality") == "missing")
    if missing:
        flags.append(f"{name}：{missing} 个点缺数")
    if detect_stuck(rs) and not (_constant_zero(rs) and sibling is not None and not detect_stuck(sibling)):
        flags.append(f"{name}：读数长时间不变，疑似卡值，已排除")
        return QualityReport(usable=[], flags=flags)
    spikes = spike_indices(rs)
    if spikes:
        flags.append(f"{name}：{len(spikes)} 个孤立尖峰，疑似干扰，已剔除")
    usable = [r for i, r in enumerate(rs) if i not in spikes and _ok(r)]
    return QualityReport(usable=usable, flags=flags)


def cross_check(primary: list[Reading], reference: list[Reading], ratio: float, tolerance: float = 1.5, margin: float = 3.0) -> bool:
    """同一风流上下游测点互相校核：primary 的最新值应约为 reference / ratio。

    例如上隅角 CO 与回风巷 CO（回风巷约为上隅角的 0.85）。若 primary 显著偏离而 reference 没有，
    判 primary 可疑（漂移）。返回 True 表示 primary 可信。

    tolerance/margin 是开发占位：原为 2.5 倍/+8 ppm，基线评测（eval/baseline.py，s6）发现上隅角 CO 漂移 +7 ppm
    时未被识别、误报蓝色，收紧为 1.5 倍/+3 ppm。上隅角 CO 真实上升可能快于回风巷，需按现场数据复核，避免把真实征兆当漂移排除。
    """
    p, r = _valid(primary), _valid(reference)
    if not p or not r:
        return True  # 无法校核时不否定
    expected = r[-1]["value"] / ratio
    got = p[-1]["value"]
    return got <= max(expected * tolerance, expected + margin)


def slope_per_hour(rs: list[Reading]) -> float | None:
    """最小二乘斜率（每小时）。少于 3 个可用点时为 None。"""
    pts = [(hours(ts(r)), r["value"]) for r in rs if r.get("value") is not None]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    den = sum((x - mx) ** 2 for x, _ in pts)
    return None if den == 0 else sum((x - mx) * (y - my) for x, y in pts) / den
