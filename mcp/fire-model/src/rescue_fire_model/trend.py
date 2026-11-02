"""单测点趋势：gas_trend 工具和研判快照（workflow 服务）共用。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from rescue_contract.client import MineData
from rescue_contract.models import Measurand

from .quality import check_series
from .warning import slope_per_hour


async def trend(md: MineData, sensor_id: str, measurand: Measurand, hours: float = 24) -> dict[str, Any]:
    """窗口内首个与最新可用值、变化量（最新减首个）、上升速率（每小时）、数据质量说明。"""
    now = await md.now()
    rs = await md.readings([sensor_id], start=now - timedelta(hours=hours), end=now, measurands=[measurand.value])
    sibling = None
    if measurand in (Measurand.C2H4, Measurand.C2H2):  # 与 assess_warning_level 一致：恒 0 时用同测点 CO 判断是否卡值
        co = await md.readings([sensor_id], start=now - timedelta(hours=hours), end=now, measurands=[Measurand.CO.value])
        sibling = sorted(co, key=lambda r: r.ts)
    rep = check_series(f"{sensor_id}/{measurand.value}", sorted(rs, key=lambda r: r.ts), sibling)
    first, last = (rep.usable[0], rep.usable[-1]) if rep.usable else (None, None)
    rate = slope_per_hour(rep.usable)
    return {
        "sensor_id": sensor_id,
        "measurand": measurand.value,
        "hours": hours,
        "points": len(rs),
        "usable_points": len(rep.usable),
        "first": first.value if first else None,
        "first_ts": first.ts.isoformat() if first else None,
        "latest": last.value if last else None,
        "latest_ts": last.ts.isoformat() if last else None,
        "change": round(last.value - first.value, 3) if first and last else None,
        "rate_per_hour": round(rate, 3) if rate is not None else None,
        "data_quality": rep.flags,
    }
