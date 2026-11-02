"""处置效果评估：evaluate_effect / evaluate_effect_panel 工具和 workflow 服务的效果评估工作流共用。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from rescue_contract.client import MineData
from rescue_contract.models import AirwayKind, Measurand, SensorSource

from . import stage, warning
from .quality import check_series


async def effect_at(md: MineData, sensor_id: str, since: datetime, measurand: Measurand, cfg: dict, now: datetime) -> dict[str, Any]:
    """单个测点单个发火指标的处置前后对比。"""
    ecfg, scfg = cfg["effect"], cfg.get("stage", {})
    flat = ecfg.get("flat_rate", {})
    if measurand.value not in flat:
        raise ToolError(f"{measurand.value} 不是发火指标，不能用于效果评估；可用：{'、'.join(flat)}")
    start = since - timedelta(hours=ecfg["baseline_h"])

    async def usable(m: Measurand, a, b, sibling: Measurand | None = None):
        rs = sorted(await md.readings([sensor_id], start=a, end=b, measurands=[m.value]), key=lambda r: r.ts)
        sib = None
        if sibling:
            sib = sorted(await md.readings([sensor_id], start=a, end=b, measurands=[sibling.value]), key=lambda r: r.ts)
        return check_series(f"{sensor_id}/{m.value}", rs, sib).usable

    before, after = await usable(measurand, start, since), await usable(measurand, since, now)
    markers = {}
    for gas in (Measurand.C2H4, Measurand.C2H2):
        markers[gas.value] = (await usable(gas, start, since, Measurand.CO), await usable(gas, since, now, Measurand.CO))
    out = stage.evaluate(since, now, before, after, markers, scfg, ecfg, flat[measurand.value])
    return {"sensor_id": sensor_id, "measurand": measurand.value, **out}


async def panel_targets(md: MineData, panel: str) -> list[tuple[str, Measurand]]:
    """工作面全部发火指标测点：采空区束管 CO、采空区测温、非进风侧安全监控 CO。"""
    net = await md.network()
    intake = {r.id for r in net.roadways if r.kind == AirwayKind.INTAKE}
    targets = []
    for sen in await md.sensors():
        if panel not in sen.id:
            continue
        if sen.source == SensorSource.BUNDLE_TUBE and Measurand.CO in sen.measurands:
            targets.append((sen.id, Measurand.CO))
        elif sen.source == SensorSource.TEMPERATURE and Measurand.TEMPERATURE in sen.measurands:
            targets.append((sen.id, Measurand.TEMPERATURE))
        elif sen.source == SensorSource.SAFETY_MONITOR and Measurand.CO in sen.measurands and sen.roadway_id not in intake:
            targets.append((sen.id, Measurand.CO))
    return targets


async def panel_effect(md: MineData, panel: str, since: datetime) -> dict[str, Any]:
    """工作面总体效果评估（逐点结果、各结论测点数、总体结论、下次复评时刻）。"""
    cfg = warning.load_thresholds()
    now = await md.now()
    targets = await panel_targets(md, panel)
    if not targets:
        raise ToolError(f"工作面 {panel} 没有可用于效果评估的发火指标测点")
    points = [await effect_at(md, sid, since, m, cfg, now) for sid, m in targets]
    return {"panel": panel, **stage.overall(points, since, now, cfg["effect"]["review_interval_h"])}
