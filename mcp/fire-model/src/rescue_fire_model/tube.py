"""单个束管测点的多组分爆炸三角形：assess_explosion_risk 工具和研判快照（workflow 服务）共用。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from rescue_contract.client import MineData
from rescue_contract.models import UNITS, Measurand, SensorPoint

from . import chart, explosion
from .quality import check_series

# 数据契约里的可燃气体参数 → explosion.yaml 的气体标识
COMBUSTIBLE = {
    Measurand.CH4: "CH4",
    Measurand.CO: "CO",
    Measurand.C2H4: "C2H4",
    Measurand.C2H2: "C2H2",
    Measurand.C2H6: "C2H6",
    Measurand.H2: "H2",
}
MARKERS = (Measurand.C2H4, Measurand.C2H2, Measurand.C2H6, Measurand.H2)  # 未出现前读数就是 0


def to_pct(m: Measurand, value: float) -> float:
    return value / 10_000 if UNITS[m] == "ppm" else value


def pair_temperature(sen: SensorPoint, sensors: dict[str, SensorPoint], chosen: str | None) -> str:
    """找与气体测点同一位置的测温点：同一巷道，且采空区深度或巷道位置相同。找不到唯一的就报错并列出候选。"""
    candidates = [s for s in sensors.values() if Measurand.TEMPERATURE in s.measurands and s.roadway_id == sen.roadway_id]
    if chosen is not None:
        t = sensors.get(chosen)
        if t is None or Measurand.TEMPERATURE not in t.measurands:
            raise ToolError(f"{chosen} 不是测温点")
        return chosen
    same = [
        s for s in candidates
        if (sen.goaf_depth_m is not None and s.goaf_depth_m == sen.goaf_depth_m)
        or (sen.position_m is not None and s.position_m == sen.position_m)
    ]  # fmt: skip
    if len(same) == 1:
        return same[0].id
    listed = "、".join(f"{s.id}（{s.name}）" for s in candidates) or "无"
    raise ToolError(f"找不到与 {sen.id} 同一位置的唯一测温点，请用 temperature_sensor_id 指定。同一巷道的测温点：{listed}")


def run_triangle(xc: explosion.Constants, sample: explosion.Sample) -> dict[str, Any]:
    try:
        return explosion.analyze(sample, xc)
    except ValueError as exc:  # 只有 ToolError 的说明能传给 Agent
        raise ToolError(str(exc)) from exc


def attach_chart(out: dict[str, Any], xc: explosion.Constants, public_url: str, label: str, subtitle: str) -> dict[str, Any]:
    """生成风险图和 JSON；存盘失败不影响判定结果本身。"""
    try:
        out["chart"] = chart.save(out, xc.air_o2, label, subtitle, public_url)
    except OSError as exc:
        out["chart"] = {"error": f"风险图未能生成：{exc}"}
    return out


async def assess_tube(
    md: MineData, sensor_id: str, xc: explosion.Constants, public_url: str, temperature_sensor_id: str | None = None
) -> dict[str, Any]:
    """用测点最近一次完整的气体分析判定爆炸危险性。输入或数据问题抛 ToolError（说明写明原因）。"""

    now = await md.now()
    sensors = {s.id: s for s in await md.sensors()}
    sen = sensors.get(sensor_id)
    if sen is None:
        raise ToolError(f"未知测点：{sensor_id}")
    fuels = [m for m in sen.measurands if m in COMBUSTIBLE]
    lacking = [m.value for m in (Measurand.O2, Measurand.CO2) if m not in sen.measurands]
    if lacking or not fuels:
        raise ToolError(f"测点 {sensor_id} 不测 {'、'.join(lacking) or '可燃气体'}，不能做爆炸三角形；请用测 O2、CO2 和可燃气体的束管测点")
    temp_id = sensor_id if Measurand.TEMPERATURE in sen.measurands else pair_temperature(sen, sensors, temperature_sensor_id)

    start = now - timedelta(hours=xc.sample_window_h)
    gas_ms = [*fuels, Measurand.O2, Measurand.CO2, *([Measurand.N2] if Measurand.N2 in sen.measurands else [])]
    rs = await md.readings([sensor_id], start=start, end=now, measurands=[m.value for m in gas_ms])
    series = {m: sorted((r for r in rs if r.measurand == m), key=lambda r: r.ts) for m in gas_ms}
    flags: list[str] = []
    usable: dict[Measurand, dict[Any, float]] = {}
    for m in gas_ms:
        sibling = series.get(Measurand.CO) if m in MARKERS else None  # 与预警判定一致：标志气体恒 0 而 CO 在变按未检出
        rep = check_series(f"{sensor_id}/{m.value}", series[m], sibling)
        flags += rep.flags
        usable[m] = {r.ts: r.value for r in rep.usable}
    required = [m for m in gas_ms if m != Measurand.N2]
    common = set.intersection(*(set(usable[m]) for m in required))
    if not common:
        raise ToolError(
            f"测点 {sensor_id} 最近 {xc.sample_window_h} 小时没有同一时刻完整可用的一组读数（需 {'、'.join(m.value for m in required)}），"
            f"不能计算。数据质量：{'；'.join(flags) or '无可用读数'}"
        )
    ts = max(common)
    n2 = usable[Measurand.N2].get(ts) if Measurand.N2 in usable else None

    trs = await md.readings([temp_id], start=start, end=now, measurands=[Measurand.TEMPERATURE.value])
    trep = check_series(f"{temp_id}/TEMP", sorted(trs, key=lambda r: r.ts))
    flags += trep.flags
    temps = [r for r in trep.usable if r.ts <= ts] or trep.usable
    if not temps:
        raise ToolError(f"测温点 {temp_id} 最近 {xc.sample_window_h} 小时没有可用温度读数，不能计算（不按假定温度代替）")
    temp = min(temps, key=lambda r: abs((r.ts - ts).total_seconds()))

    out = run_triangle(
        xc,
        explosion.Sample(
            combustibles={COMBUSTIBLE[m]: to_pct(m, usable[m][ts]) for m in fuels},
            o2=usable[Measurand.O2][ts],
            co2=usable[Measurand.CO2][ts],
            temperature_c=temp.value,
            n2=n2,
        ),
    )
    out["sample"] = {
        "sensor_id": sensor_id,
        "sensor_name": sen.name,
        "ts": ts.isoformat(),
        "readings": {m.value: usable[m][ts] for m in required} | ({"N2": n2} if n2 is not None else {}),
        "units": {m.value: UNITS[m] for m in required},
        "temperature_sensor_id": temp_id,
        "temperature_ts": temp.ts.isoformat(),
    }
    out["data_quality"] = flags
    return attach_chart(
        out,
        xc,
        public_url,
        f"{sensor_id}-{ts:%Y%m%dT%H%M}",
        f"{sen.name}（{sensor_id}）· 取样时刻 {ts:%Y-%m-%d %H:%M} · 温度取自 {temp_id}",
    )
