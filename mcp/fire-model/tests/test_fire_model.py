import asyncio

import pytest
from rescue_contract.client import MineData
from rescue_contract.models import ExplosionRisk, WarningLevel
from rescue_fire_model import alarms, explosion, warning
from rescue_mine_data_sim.server import SimHolder
from rescue_mine_data_sim.server import build_server as build_mine


def sim(scenario: str, hours: float):
    holder = SimHolder(scenario, manual_clock=True)
    holder.engine.clock.advance(hours)
    return holder, MineData(build_mine(holder))


def level(scenario: str, hours: float) -> warning.Assessment:
    _, md = sim(scenario, hours)
    return asyncio.run(warning.assess(md))


# ── 爆炸三角形 ──


@pytest.mark.parametrize(
    "ch4,o2,expected",
    [
        (8.0, 18.0, ExplosionRisk.EXPLOSIVE),
        (30.0, 10.0, ExplosionRisk.MAY_BECOME_EXPLOSIVE),
        (1.0, 20.5, ExplosionRisk.NOT_EXPLOSIVE),
        (12.0, 8.0, ExplosionRisk.MAY_BECOME_EXPLOSIVE),  # 惰化区，但补入空气会进入爆炸范围
        (8.0, 8.0, ExplosionRisk.NOT_EXPLOSIVE),  # 补入空气的路径从鼻点左侧经过
        (3.0, 5.0, ExplosionRisk.NOT_EXPLOSIVE),
    ],
)
def test_explosion_triangle(ch4, o2, expected):
    assert explosion.classify(ch4, o2) == expected


def test_graham_ratio():
    assert warning.graham_ratio(1000, 15.0, 79.0) == pytest.approx(100 * 0.1 / (0.265 * 79 - 15), rel=1e-6)
    assert warning.graham_ratio(1000, 20.9, 79.0) is None  # 无氧耗，不计算


# ── 预警分级在标准场景上的表现（回归基线）──


def test_s1_normal_does_not_warn_yellow_or_above():
    for h in (12, 36, 60):
        a = level("s1-normal", h)
        assert warning.level_index(a.level) <= warning.level_index(WarningLevel.BLUE), (h, a.to_dict())


def test_s2_escalates_without_action():
    early, late = level("s2-slow-oxidation", 12), level("s2-slow-oxidation", 84)
    assert warning.level_index(late.level) > warning.level_index(early.level)
    assert warning.level_index(late.level) >= warning.level_index(WarningLevel.ORANGE)


def test_s6_faults_detected_and_not_escalated():
    a = level("s6-sensor-faults", 28)  # 上隅角 CO 漂移期间
    assert warning.level_index(a.level) <= warning.level_index(WarningLevel.BLUE), a.to_dict()
    assert any("漂移" in f for f in a.data_quality)
    b = level("s6-sensor-faults", 40.5)  # 尖峰期间
    assert warning.level_index(b.level) <= warning.level_index(WarningLevel.BLUE), b.to_dict()
    c = level("s6-sensor-faults", 36)  # 束管 2 号卡值
    assert any("卡值" in f for f in c.data_quality)


def test_s5_reignition_alarm_after_early_unsealing():
    holder, md = sim("s5-sealed-fire-area", 240)
    assert asyncio.run(alarms.check_reignition(md)) == []  # 封闭期间无复燃征兆
    holder.engine.apply("unseal")
    holder.engine.clock.advance(72)
    found = asyncio.run(alarms.check_reignition(md))
    assert any(a.rule_id == "RL-164" for a in found)


# ── 标志气体恒为 0：未检出 vs 卡值 ──


def _rs(measurand: str, values: list[float]):
    from datetime import datetime, timedelta

    from rescue_contract.models import Reading

    t0 = datetime(2026, 11, 2, 8, 0)
    return [Reading(sensor_id="BT-X", measurand=measurand, ts=t0 + timedelta(hours=i), value=v) for i, v in enumerate(values)]


def test_constant_zero_with_working_sibling_is_not_stuck():
    from rescue_fire_model.quality import check_series

    zeros = _rs("C2H4", [0.0] * 8)
    co_moving = _rs("CO", [10.0 + 0.3 * i for i in range(8)])
    co_stuck = _rs("CO", [12.0] * 8)
    assert len(check_series("x", zeros, co_moving).usable) == 8  # 测点在工作：未检出
    assert check_series("x", zeros, co_stuck).usable == []  # 整个测点卡住：仍排除
    assert check_series("x", zeros).usable == []  # 无从判断：仍排除（fail closed）
    assert check_series("x", _rs("C2H4", [0.4] * 8), co_moving).usable == []  # 卡在非 0 值：照旧排除


def test_s3_ethylene_not_yet_present_reads_zero_not_stuck():
    a = level("s3-accelerating", 20)  # 高温点尚未到出现乙烯的温度
    assert a.indicators["c2h4_ppm"] == 0.0, a.to_dict()
    assert a.indicators["c2h2_ppm"] == 0.0, a.to_dict()
    assert not any("C2H4" in f or "C2H2" in f for f in a.data_quality), a.data_quality


def _gas_trend(scenario: str, hours: float, sensor_id: str, measurand: str) -> dict:
    from mcp.client import Client
    from rescue_fire_model.server import build_server

    holder, _ = sim(scenario, hours)

    async def go():
        async with Client(build_server(build_mine(holder))) as c:
            return (await c.call_tool("gas_trend", {"sensor_id": sensor_id, "measurand": measurand})).structured_content

    return asyncio.run(go())


def test_s6_stuck_tube_still_excludes_its_ethylene():
    c = level("s6-sensor-faults", 36)
    assert "BT-1201-G2/CO：读数长时间不变，疑似卡值，已排除" in c.data_quality  # 整根束管卡住，assess 跳过该管
    r = _gas_trend("s6-sensor-faults", 36, "BT-1201-G2", "C2H4")
    assert r["usable_points"] == 0 and r["latest"] is None and any("卡值" in f for f in r["data_quality"]), r


def test_gas_trend_ethylene_zero_is_usable():
    r = _gas_trend("s3-accelerating", 20, "BT-1201-G1", "C2H4")
    assert r["latest"] == 0.0 and r["usable_points"] > 0 and r["data_quality"] == [], r


# ── 第164条只对火区台账中的火区适用 ──


class _NoRegister(MineData):
    """模拟取不到火区台账的数据源。"""

    async def fire_areas(self):
        from rescue_contract.client import MineDataError

        raise MineDataError("mine_data.get_fire_areas 失败：未实现")


def test_reignition_not_reported_for_never_sealed_area():
    holder, md = sim("s3-accelerating", 28)  # CO 快速上升，但 1201 从未封闭
    assert asyncio.run(alarms.check_reignition(md)) == []
    fallback = _NoRegister(build_mine(holder))
    assert any(a.rule_id == "RL-164" for a in asyncio.run(alarms.check_reignition(fallback)))  # 台账取不到时照常检查


def test_assess_warning_level_includes_realtime_alarms():
    from mcp.client import Client
    from rescue_fire_model.server import build_server

    holder, _ = sim("s5-sealed-fire-area", 240)
    holder.engine.apply("unseal")
    holder.engine.clock.advance(72)

    async def go():
        async with Client(build_server(build_mine(holder))) as c:
            return (await c.call_tool("assess_warning_level", {})).structured_content

    r = asyncio.run(go())
    assert any(a["rule_id"] == "RL-164" for a in r["realtime_alarms"]), r
