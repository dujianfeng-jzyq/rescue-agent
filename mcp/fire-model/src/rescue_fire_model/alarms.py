"""实时红线报警（不依赖大模型）。

这些检查应由独立的监视进程周期性运行，触发后直接推送给值班人员，同时进入执行日志。
目前提供检查函数和 MCP 工具；周期运行和推送通道尚未实现。

TODO(工程): 实现监视进程（例如每分钟拉取一次最新读数），以及推送到调度台和移动端的通道。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from rescue_contract.client import MineData
from rescue_contract.models import Measurand

from .quality import check_series
from .warning import slope_per_hour


@dataclass(frozen=True)
class Alarm:
    rule_id: str
    article: str
    sensor_id: str
    message: str


async def check_methane(md: MineData, sensor_ids: list[str] | None = None, limit_pct: float = 2.0) -> list[Alarm]:
    """第75条：甲烷超过 2% 且继续上升时，应立即撤到安全地点。"""
    now = await md.now()
    sensors = [s for s in await md.sensors() if Measurand.CH4 in s.measurands]
    if sensor_ids:
        sensors = [s for s in sensors if s.id in sensor_ids]
    # 采空区束管本身甲烷就高，这条规则针对人员可能所在的巷道测点
    sensors = [s for s in sensors if not s.id.startswith("BT-")]
    readings = await md.readings([s.id for s in sensors], start=now - timedelta(hours=1), end=now, measurands=["CH4"])
    out = []
    for s in sensors:
        series = sorted((r for r in readings if r.sensor_id == s.id), key=lambda r: r.ts)
        usable = check_series(s.id, series).usable
        if not usable:
            continue
        last = usable[-1].value
        rate = slope_per_hour(usable)
        if last > limit_pct and (rate is None or rate > 0):
            out.append(Alarm("RL-075", "第75条", s.id, f"{s.name} 甲烷 {last:.2f}% 超过 {limit_pct}% 且在上升，人员应立即撤到安全地点"))
    return out


async def check_reignition(md: MineData, panel: str = "1201", co_rate_ppm_h: float = 0.8, co_growth_ratio: float = 1.3) -> list[Alarm]:
    """第164条相关：启封后（或封闭火区内）束管 CO 持续上升，视为复燃征兆。

    判据：近 12 小时 CO 上升速率 ≥ co_rate_ppm_h，且最新值 ≥ 窗口起点的 co_growth_ratio 倍。
    TODO(专家): 判据和参数需按本矿火区管理经验确定。
    """
    now = await md.now()
    tubes = [s for s in await md.sensors() if s.id.startswith(f"BT-{panel}")]
    readings = await md.readings([s.id for s in tubes], start=now - timedelta(hours=12), end=now, measurands=["CO"])
    out = []
    for t in tubes:
        usable = check_series(t.id, sorted((r for r in readings if r.sensor_id == t.id), key=lambda r: r.ts)).usable
        rate = slope_per_hour(usable)
        if rate is None or len(usable) < 3:
            continue
        growth = usable[-1].value / max(usable[0].value, 1e-6)
        if rate >= co_rate_ppm_h and growth >= co_growth_ratio:
            out.append(
                Alarm("RL-164", "第164条", t.id, f"{t.name} CO 近12小时以 {rate:.1f} ppm/h 上升，可能复燃，应立即重新封闭火区并撤人")
            )
    return out
