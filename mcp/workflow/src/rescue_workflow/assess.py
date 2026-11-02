"""W1 研判快照：步骤固定，取数和汇总全部由代码完成（docs/workflow-design.md W1）。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from rescue_contract.client import MineData
from rescue_contract.models import AirwayKind, Measurand, SensorSource, WarningLevel
from rescue_fire_model import alarms, explosion, trend, tube, warning
from rescue_ventnet import network

from . import config

EXPLOSION_NAMES = {
    "explosive": "处于爆炸范围",
    "may_become_explosive": "可能进入爆炸范围（减风积聚或混入空气）",
    "not_explosive": "当前不在爆炸范围",
    "unknown": "无法判定（有测点算不出来，按有爆炸危险对待）",
}
LEVELS = [lv.value for lv in WarningLevel]  # none < blue < yellow < orange < red


def level_at_least(level: str, floor: str) -> bool:
    return LEVELS.index(level) >= LEVELS.index(floor)


async def realtime_alarms(md: MineData, panel: str) -> list[dict[str, Any]]:
    found = await alarms.check_methane(md) + await alarms.check_reignition(md, panel)
    return [asdict(a) for a in found]


def trend_targets(w: warning.Assessment, cfg: dict[str, Any]) -> list[tuple[str, Measurand]]:
    """触发条件涉及的指标的来源测点；没有触发时取默认指标。同一测点同一参数只算一次。"""
    keys = [k for k, v in w.indicator_sources.items() if any(warning.LABELS[k] in t for t in w.triggered)]
    if not keys:
        keys = [k for k in cfg["default_trend_indicators"] if k in w.indicator_sources]
    out: list[tuple[str, Measurand]] = []
    for k in keys:
        src = w.indicator_sources[k]
        pair = (src["sensor_id"], Measurand(src["measurand"]))
        if pair not in out:
            out.append(pair)
    return out


async def explosion_panel(md: MineData, panel: str, xc: explosion.Constants, public_url: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """本工作面全部同时测 O2、CO2 和可燃气体的束管测点逐个计算，取最不利；任一失败为 unknown。"""
    tubes = [
        s.id for s in await md.sensors()
        if panel in s.id and s.source == SensorSource.BUNDLE_TUBE
        and {Measurand.O2, Measurand.CO2} <= set(s.measurands) and set(s.measurands) & set(tube.COMBUSTIBLE)
    ]  # fmt: skip
    points, failures = [], []
    for sid in tubes:
        try:
            r = await tube.assess_tube(md, sid, xc, public_url)
        except ToolError as exc:
            failures.append({"sensor_id": sid, "error": str(exc)})
            continue
        points.append({
            "sensor_id": sid,
            "zone": r["zone"],
            "zone_name": r["zone_name"],
            "explosion_risk": r["explosion_risk"],
            "meaning": r["meaning"],
            "warnings": r["warnings"],
            "sample": r["sample"],
            "data_quality": r["data_quality"],
            "chart": r.get("chart", {}),
        })  # fmt: skip
    order = cfg["explosion_order"]
    worst = min(points, key=lambda p: order.index(p["explosion_risk"])) if points else None
    overall = "unknown" if failures or not points else worst["explosion_risk"]
    for p in points:  # 风险图只保留最不利测点的，减小结果体积；其余测点的图可用 assess_explosion_risk 单独取
        if p is not worst:
            p.pop("chart")
    return {
        "overall": overall,
        "overall_name": EXPLOSION_NAMES[overall],
        "worst_sensor_id": worst["sensor_id"] if worst else None,
        "points": points,
        "failures": failures,
        "chart_markdown": (worst or {}).get("chart", {}).get("markdown"),
    }


async def personnel_source(md: MineData, panel: str, kind: str) -> str:
    net = await md.network()
    want = AirwayKind(kind)
    for r in net.roadways:
        if r.kind == want and panel in r.id:
            return r.id
    raise ToolError(f"网络中找不到工作面 {panel} 的 {kind} 巷道，不能计算受威胁人员")


async def assess_panel(
    md: MineData, panel: str, xc: explosion.Constants, public_url: str, include_personnel: bool = False
) -> dict[str, Any]:
    cfg = config.load()["assessment"]
    if not any(panel in s.id for s in await md.sensors()):  # 编号写错时不能返回“预警等级 无”，那会被读成“没事”
        raise ToolError(f"找不到工作面 {panel} 的测点，请核对工作面编号")
    now = await md.now()
    alarm_list = await realtime_alarms(md, panel)
    w = await warning.assess(md, panel)
    if w.as_of != now:  # 模拟时钟在走时两次取数可能不同；以预警判定的时刻为准
        now = w.as_of
    trends = [await trend.trend(md, sid, m, cfg["trend_hours"]) for sid, m in trend_targets(w, cfg)]
    xr = await explosion_panel(md, panel, xc, public_url, cfg)
    personnel, personnel_note = None, None
    want = include_personnel or level_at_least(w.level.value, cfg["personnel_level_at_least"])
    if want:
        source = await personnel_source(md, panel, cfg["personnel_source_kind"])
        personnel = network.threatened(await md.network(), source, await md.personnel())
    else:
        personnel_note = (
            f"预警等级未达到{warning.NAMES.get(cfg['personnel_level_at_least'], cfg['personnel_level_at_least'])}，未计算受威胁人员"
        )
    return {
        "panel": panel,
        "as_of": now.isoformat(),
        "realtime_alarms": alarm_list,
        "warning": w.to_dict(),
        "trends": trends,
        "explosion": xr,
        "personnel": personnel,
        "personnel_note": personnel_note,
    }
