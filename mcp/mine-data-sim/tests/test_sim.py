import asyncio

import pytest
from rescue_contract.client import MineData
from rescue_contract.conformance import run_all
from rescue_contract.models import Measurand, Quality
from rescue_mine_data_sim.server import SimHolder, build_server


def make(scenario: str, hours: float = 0):
    holder = SimHolder(scenario, manual_clock=True)
    holder.engine.clock.advance(hours)
    return holder, build_server(holder)


def test_all_scenarios_load():
    holder = SimHolder("s1-normal", manual_clock=True)
    assert {"s1-normal", "s2-slow-oxidation", "s3-accelerating", "s4-escalation", "s5-sealed-fire-area", "s6-sensor-faults"} <= set(
        holder.scenarios
    )


@pytest.mark.parametrize("scenario", ["s1-normal", "s2-slow-oxidation", "s5-sealed-fire-area", "s6-sensor-faults"])
def test_conformance(scenario):
    _, server = make(scenario, 30)
    results = asyncio.run(run_all(server))
    assert [r for r in results if r[1]] == []


def _uc_co(holder, h):
    return holder.engine.reading("SM-1201-UC", Measurand.CO, h).value


def test_s2_co_rises_without_action():
    holder, _ = make("s2-slow-oxidation")
    assert _uc_co(holder, 84) > 3 * _uc_co(holder, 0)


def test_s2_inerting_and_leak_sealing_contain_heating():
    untreated, _ = make("s2-slow-oxidation", 48)
    treated, _ = make("s2-slow-oxidation", 48)
    treated.engine.apply("inert_injection", {"rate_m3_h": 1000})
    treated.engine.apply("leak_sealing", {"reduction": 0.5})
    for h in (untreated, treated):
        h.engine.clock.advance(48)
    assert treated.engine.hotspot()["temperature_c"] < untreated.engine.hotspot()["temperature_c"] - 50


def test_s5_sealed_sensors_report_missing_and_area_cools():
    holder, _ = make("s5-sealed-fire-area", 1)
    t0 = holder.engine.hotspot()["temperature_c"]
    uc = holder.engine.latest(["SM-1201-UC"])
    assert all(r.quality == Quality.MISSING for r in uc)
    holder.engine.clock.advance(120)
    assert holder.engine.hotspot()["temperature_c"] < t0 / 2


def test_s6_dropout_is_marked_missing_but_drift_is_not():
    holder, _ = make("s6-sensor-faults", 40)
    dropped = holder.engine.reading("SM-1201-RET", Measurand.CO, 32)
    drifted = holder.engine.reading("SM-1201-UC", Measurand.CO, 28)
    assert dropped.quality == Quality.MISSING
    assert drifted.quality == Quality.OK and drifted.value > 15  # 故障值看起来像真实读数


def test_readings_are_deterministic():
    a, _ = make("s2-slow-oxidation", 24)
    b, _ = make("s2-slow-oxidation", 24)
    ra = a.engine.readings(["BT-1201-G2"], 0, 24)
    rb = b.engine.readings(["BT-1201-G2"], 0, 24)
    assert [r.value for r in ra] == [r.value for r in rb]


def test_client_roundtrip_in_process():
    _, server = make("s2-slow-oxidation", 30)

    async def go():
        md = MineData(server)
        now = await md.now()
        latest = await md.latest(["SM-1201-UC"])
        people = await md.personnel()
        return now, latest, people

    now, latest, people = asyncio.run(go())
    assert latest and all(r.ts <= now for r in latest)
    assert people


# ── 火区台账 ──


def test_fire_areas_register_follows_sealing_and_unsealing():
    from rescue_contract.models import FireAreaStatus

    holder, server = make("s3-accelerating", 20)
    md = MineData(server)
    assert asyncio.run(md.fire_areas()) == []  # 从未封闭的发火区域不在台账里

    holder, server = make("s5-sealed-fire-area", 24)
    md = MineData(server)
    (a,) = asyncio.run(md.fire_areas())
    assert a.panel == "1201" and a.status == FireAreaStatus.SEALED and a.unsealed_at is None
    sealed_at = a.sealed_at

    holder.engine.apply("unseal")
    holder.engine.clock.advance(1)
    (a,) = asyncio.run(md.fire_areas())
    assert a.status == FireAreaStatus.UNSEALED and a.sealed_at == sealed_at and a.unsealed_at is not None
    assert [err for _, err in asyncio.run(run_all(server)) if err] == []  # 启封后仍通过一致性检查

    holder.engine.apply("temporary_seal")  # 重新封闭：启封时间清空，封闭时间更新
    holder.engine.clock.advance(1)
    (a,) = asyncio.run(md.fire_areas())
    assert a.status == FireAreaStatus.SEALED and a.unsealed_at is None and a.sealed_at > sealed_at
