"""数据质量检测：在研判前识别卡值、尖峰、漂移等故障读数。

真实监控系统不会自报漂移和卡值，所以这一步必须由我们做。被判为可疑的读数不参与预警分级，
并在研判结论里列出。标准场景 s6 专门检验这里。

TODO(算法): 现在是简单规则，阶段 2 用历史数据调整阈值，必要时引入统计模型。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from rescue_contract.models import Quality, Reading


@dataclass
class QualityReport:
    usable: list[Reading]
    flags: list[str] = field(default_factory=list)  # 给人看的说明


def _valid(rs: list[Reading]) -> list[Reading]:
    return [r for r in rs if r.quality == Quality.OK and r.value is not None]


def detect_stuck(rs: list[Reading], min_points: int = 6) -> bool:
    """最近 min_points 个点完全不变，视为卡值。"""
    vals = [r.value for r in _valid(rs)][-min_points:]
    return len(vals) >= min_points and max(vals) - min(vals) < 1e-9


def spike_indices(rs: list[Reading], factor: float = 4.0, floor: float = 5.0) -> set[int]:
    """单点超过前后邻居中位数的 factor 倍（且超过 floor），视为尖峰。"""
    vals = [r.value for r in rs]
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


def check_series(name: str, rs: list[Reading]) -> QualityReport:
    flags = []
    missing = sum(1 for r in rs if r.quality == Quality.MISSING)
    if missing:
        flags.append(f"{name}：{missing} 个点缺数")
    if detect_stuck(rs):
        flags.append(f"{name}：读数长时间不变，疑似卡值，已排除")
        return QualityReport(usable=[], flags=flags)
    spikes = spike_indices(rs)
    if spikes:
        flags.append(f"{name}：{len(spikes)} 个孤立尖峰，疑似干扰，已剔除")
    usable = [r for i, r in enumerate(rs) if i not in spikes and r.quality == Quality.OK and r.value is not None]
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
    expected = r[-1].value / ratio
    got = p[-1].value
    return got <= max(expected * tolerance, expected + margin)
