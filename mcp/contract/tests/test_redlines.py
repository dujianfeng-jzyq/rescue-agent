"""红线规则测试。每条规则至少一个拦截用例和一个放行用例。

新增或修改规则时，先在这里写用例，再改 redlines.py；用例需经领域专家确认。
"""

from datetime import datetime, timedelta

import pytest
from rescue_contract.models import (
    ExplosionRisk,
    FireContext,
    FireType,
    Plan,
    PlanKind,
    PlanStep,
    StepType,
)
from rescue_contract.redlines import RULES, check_plan, format_violations

NOW = datetime(2026, 11, 1, 8, 0)


def ctx(**kw) -> FireContext:
    base = dict(
        fire_type=FireType.GOAF_SPONTANEOUS,
        as_of=NOW,
        methane_pct=0.4,
        methane_rising=False,
        o2_pct=12.0,
        area_temperature_c=28.0,
        visibility_m=5.0,
        explosion_risk=ExplosionRisk.NOT_EXPLOSIVE,
    )
    base.update(kw)
    return FireContext(**base)


def plan(kind: PlanKind, steps: list[tuple[StepType, dict]], **ctx_kw) -> Plan:
    return Plan(
        plan_id="P-TEST",
        kind=kind,
        title="测试方案",
        context=ctx(**ctx_kw),
        steps=[PlanStep(order=i + 1, type=t, description=t.value, **kw) for i, (t, kw) in enumerate(steps)],
    )


def ids(p: Plan) -> set[str]:
    return {v.rule_id for v in check_plan(p)}


def test_every_rule_cites_an_article():
    assert RULES
    for r in RULES:
        assert r.article.startswith("第"), r.rule_id


def test_typical_firefighting_plan_passes():
    p = plan(
        PlanKind.FIREFIGHTING,
        [(StepType.MONITOR, {}), (StepType.INERT_INJECTION, {}), (StepType.GROUTING, {}), (StepType.LEAK_SEALING, {})],
    )
    assert check_plan(p) == []


# RL-077 反风前撤人
def test_rl077_reverse_without_evacuation_blocked():
    p = plan(PlanKind.FIREFIGHTING, [(StepType.REVERSE_VENTILATION, {})], fire_type=FireType.BELT_ROADWAY)
    assert "RL-077" in ids(p)


def test_rl077_reverse_after_evacuation_allowed():
    p = plan(
        PlanKind.FIREFIGHTING,
        [(StepType.EVACUATE, {}), (StepType.REVERSE_VENTILATION, {})],
        fire_type=FireType.BELT_ROADWAY,
    )
    assert "RL-077" not in ids(p)


# RL-098 采空区火灾保持通风稳定
def test_rl098_goaf_fire_blocks_ventilation_changes():
    p = plan(PlanKind.SEALING, [(StepType.EVACUATE, {}), (StepType.STOP_MAIN_FAN, {})])
    assert "RL-098" in ids(p)


def test_rl098_not_applied_to_belt_fire():
    p = plan(
        PlanKind.FIREFIGHTING,
        [(StepType.EVACUATE, {}), (StepType.SHORT_CIRCUIT_VENTILATION, {})],
        fire_type=FireType.BELT_ROADWAY,
    )
    assert "RL-098" not in ids(p)


# RL-079 用水灭火条件
def test_rl079_water_without_confirmed_conditions_blocked():
    p = plan(PlanKind.FIREFIGHTING, [(StepType.WATER_FIREFIGHTING, {})])
    v = [x for x in check_plan(p) if x.rule_id == "RL-079"]
    assert v and "火源明确" in v[0].message


def test_rl079_water_with_all_conditions_allowed():
    p = plan(
        PlanKind.FIREFIGHTING,
        [(StepType.WATER_FIREFIGHTING, {})],
        fire_source_confirmed=True,
        water_supply_sufficient=True,
        return_airway_clear=True,
    )
    assert "RL-079" not in ids(p)


# RL-084 / RL-085 封闭顺序与注惰抑爆
def test_rl084_permanent_seal_requires_temporary_first():
    p = plan(PlanKind.SEALING, [(StepType.PERMANENT_SEAL, {})])
    assert "RL-084" in ids(p)


@pytest.mark.parametrize("risk", [ExplosionRisk.EXPLOSIVE, ExplosionRisk.MAY_BECOME_EXPLOSIVE, ExplosionRisk.UNKNOWN])
def test_rl085_seal_requires_inerting_when_risk_not_ruled_out(risk):
    p = plan(PlanKind.SEALING, [(StepType.TEMPORARY_SEAL, {}), (StepType.PERMANENT_SEAL, {})], explosion_risk=risk)
    assert "RL-085" in ids(p)


def test_rl085_inert_before_seal_allowed():
    p = plan(
        PlanKind.SEALING,
        [(StepType.EVACUATE, {}), (StepType.INERT_INJECTION, {}), (StepType.TEMPORARY_SEAL, {}), (StepType.PERMANENT_SEAL, {})],
        explosion_risk=ExplosionRisk.EXPLOSIVE,
    )
    assert ids(p) == set()


# RL-057 / RL-053 进入灾区人数与待机小队
def test_rl057_enter_with_small_team_blocked():
    p = plan(PlanKind.FIREFIGHTING, [(StepType.ENTER_DISASTER_AREA, {"team_size": 4, "standby_team": True})])
    assert "RL-057" in ids(p)


def test_rl053_enter_without_standby_blocked():
    p = plan(PlanKind.FIREFIGHTING, [(StepType.ENTER_DISASTER_AREA, {"team_size": 6})])
    assert "RL-053" in ids(p)


def test_enter_with_proper_team_allowed():
    p = plan(PlanKind.FIREFIGHTING, [(StepType.ENTER_DISASTER_AREA, {"team_size": 6, "standby_team": True})])
    assert ids(p) == set()


# RL-088 高温浓烟
def test_rl088_enter_above_40c_blocked():
    p = plan(
        PlanKind.FIREFIGHTING,
        [(StepType.ENTER_DISASTER_AREA, {"team_size": 6, "standby_team": True})],
        area_temperature_c=45.0,
    )
    assert "RL-088" in ids(p)


def test_rl088_unknown_temperature_fails_closed():
    p = plan(
        PlanKind.FIREFIGHTING,
        [(StepType.ENTER_DISASTER_AREA, {"team_size": 6, "standby_team": True})],
        area_temperature_c=None,
    )
    assert "RL-088" in ids(p)


def test_rl088_low_visibility_blocked():
    p = plan(
        PlanKind.FIREFIGHTING,
        [(StepType.ENTER_DISASTER_AREA, {"team_size": 6, "standby_team": True})],
        visibility_m=0.5,
    )
    assert "RL-088" in ids(p)


# RL-075 甲烷超限
def test_rl075_rising_methane_blocks_entry():
    p = plan(
        PlanKind.FIREFIGHTING,
        [(StepType.ENTER_DISASTER_AREA, {"team_size": 6, "standby_team": True})],
        methane_pct=2.5,
        methane_rising=True,
    )
    assert "RL-075" in ids(p)


# RL-087 封闭后 24 小时
def test_rl087_inspect_within_24h_blocked():
    p = plan(
        PlanKind.SEALING,
        [(StepType.INSPECT_SEALED_AREA, {"team_size": 6, "standby_team": True})],
        sealed_at=NOW - timedelta(hours=10),
    )
    assert "RL-087" in ids(p)


def test_rl087_inspect_after_24h_allowed():
    p = plan(
        PlanKind.SEALING,
        [(StepType.INSPECT_SEALED_AREA, {"team_size": 6, "standby_team": True})],
        sealed_at=NOW - timedelta(hours=30),
    )
    assert "RL-087" not in ids(p)


# RL-164 启封
def test_rl164_unseal_without_prechecks_blocked():
    p = plan(PlanKind.UNSEALING, [(StepType.UNSEAL, {})])
    v = [x for x in check_plan(p) if x.rule_id == "RL-164"]
    assert v and "切断回风流电源" in v[0].message


def test_rl164_unseal_with_prechecks_allowed():
    p = plan(
        PlanKind.UNSEALING,
        [(StepType.MONITOR, {}), (StepType.POWER_CUTOFF, {}), (StepType.EVACUATE, {}), (StepType.UNSEAL, {})],
    )
    assert ids(p) == set()


def test_format_violations_lists_articles():
    p = plan(PlanKind.SEALING, [(StepType.PERMANENT_SEAL, {})], explosion_risk=ExplosionRisk.EXPLOSIVE)
    text = format_violations(check_plan(p))
    assert "第84条" in text and "第85条" in text
