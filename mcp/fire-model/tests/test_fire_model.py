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
