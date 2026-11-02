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

XC = explosion.load_constants()


def triangle(gases: dict[str, float], o2: float, co2: float = 0.05, t: float = 25.0, n2: float | None = None) -> dict:
    return explosion.analyze(explosion.Sample(gases, o2, co2, t, n2), XC)


@pytest.mark.parametrize(
    "ch4,o2,zone,risk",
    [
        (8.0, 18.0, "I", ExplosionRisk.EXPLOSIVE),
        (0.042, 19.8, "II", ExplosionRisk.MAY_BECOME_EXPLOSIVE),  # 新鲜风流：甲烷积聚会进入爆炸范围
        (8.0, 8.0, "III", ExplosionRisk.MAY_BECOME_EXPLOSIVE),  # 惰化区，补入空气会穿过三角形
        (30.0, 10.0, "III", ExplosionRisk.MAY_BECOME_EXPLOSIVE),
        (3.0, 8.0, "IV", ExplosionRisk.NOT_EXPLOSIVE),  # 鼻点以下的贫氧区；原脚本把这里判成 II
        (0.5, 2.0, "IV", ExplosionRisk.NOT_EXPLOSIVE),
    ],
)
def test_explosion_zones(ch4, o2, zone, risk):
    r = triangle({"CH4": ch4}, o2)
    assert (r["zone"], r["explosion_risk"]) == (zone, risk.value)


def test_explosion_triangle_matches_skill_example():
    """与 explosion-triangle-synthesis 技能自检算例的顶点一致（CO2 = 0，N2 用 O2 当量）。"""
    r = triangle({"CH4": 4.0, "CO": 2.0, "H2": 1.0}, 18.0, co2=0.0, t=30.0)
    assert r["temperature_factor"] == pytest.approx(0.003605)
    assert r["triangle"]["lower"]["combustible_pct"] == pytest.approx(6.974765, abs=1e-4)
    assert r["triangle"]["upper"]["combustible_pct"] == pytest.approx(40.516968, abs=1e-4)
    assert r["triangle"]["critical"]["combustible_pct"] == pytest.approx(7.291429, abs=1e-4)
    assert r["inert"] == {"co2_pct": 0.0, "n2_weight": pytest.approx(67.14), "n2_source": "o2_equivalent"}
    assert r["zone"] == "II"


def test_other_combustibles_change_the_conclusion():
    """只算甲烷会偏乐观：计入 H2 后状态点进入三角形。"""
    assert triangle({"CH4": 4.5}, 18.0)["zone"] == "II"
    assert triangle({"CH4": 4.5, "H2": 1.5}, 18.0)["zone"] == "I"


def test_temperature_widens_only_lower_and_upper():
    cold, hot = triangle({"CH4": 1.0}, 10.0, t=25.0, n2=70.0), triangle({"CH4": 1.0}, 10.0, t=80.0, n2=70.0)
    assert hot["triangle"]["lower"]["combustible_pct"] < cold["triangle"]["lower"]["combustible_pct"]
    assert hot["triangle"]["upper"]["combustible_pct"] > cold["triangle"]["upper"]["combustible_pct"]
    assert hot["triangle"]["critical"] == cold["triangle"]["critical"]


def test_n2_measured_used_and_inconsistent_n2_dropped():
    assert triangle({"CH4": 3.0}, 8.0, co2=1.0, n2=87.0)["inert"]["n2_source"] == "measured"
    r = triangle({"CH4": 3.0}, 8.0, co2=1.0, n2=92.0)  # 合计 104%
    assert r["inert"]["n2_source"] == "o2_equivalent"
    assert any("不采用" in w for w in r["warnings"])


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"gases": {"CH4": 3.0}, "o2": 10.0, "t": 200.0}, "适用范围"),
        ({"gases": {"CH4": 0.0}, "o2": 10.0}, "未检出"),
        ({"gases": {"H2S": 1.0}, "o2": 10.0}, "H2S"),
        ({"gases": {"CH4": 50.0}, "o2": 60.0}, "超过 100%"),
        ({"gases": {"CH4": 3.0}, "o2": 0.0, "co2": 0.0}, "S 点"),
    ],
)
def test_explosion_refuses_incomplete_or_invalid_input(kwargs, message):
    with pytest.raises(ValueError, match=message):
        triangle(**kwargs)


def _call_fire(holder, tool: str, args: dict):
    from mcp.client import Client
    from rescue_fire_model.server import build_server

    async def go():
        async with Client(build_server(build_mine(holder))) as c:
            return await c.call_tool(tool, args)

    return asyncio.run(go())


def test_assess_explosion_risk_reads_bundle_tube_sample():
    holder, _ = sim("s4-escalation", 80)
    r = _call_fire(holder, "assess_explosion_risk", {"sensor_id": "BT-1201-G1"})
    assert not r.is_error, r.content[0].text
    d = r.structured_content
    sample = d["sample"]
    assert sample["temperature_sensor_id"] == "TP-1201-G1"  # 按采空区深度配对
    assert d["combustibles_pct"]["CO"] == pytest.approx(sample["readings"]["CO"] / 10_000, rel=1e-4)  # ppm 换算成 %
    assert "C2H4" in d["combustibles_pct"]  # 乙烯已出现，计入合成
    assert d["zone"] in {"I", "II", "III", "IV"}


def test_assess_explosion_risk_refuses_stuck_sensor():
    holder, _ = sim("s6-sensor-faults", 30)
    r = _call_fire(holder, "assess_explosion_risk", {"sensor_id": "BT-1201-G2"})
    assert r.is_error and "卡值" in r.content[0].text


def test_assess_explosion_risk_sample_converts_ppm():
    holder, _ = sim("s2-slow-oxidation", 1)
    args = {"ch4_pct": 4.5, "co_ppm": 15000, "o2_pct": 18.0, "co2_pct": 0.05, "temperature_c": 25.0}
    d = _call_fire(holder, "assess_explosion_risk_sample", args).structured_content
    assert d["combustibles_pct"] == {"CH4": 4.5, "CO": 1.5}
    assert any("未提供" in w for w in d["warnings"])


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
    assert r["usable_points"] == 0 and r["latest"] is None and r["change"] is None and any("卡值" in f for f in r["data_quality"]), r


def test_gas_trend_reports_change_over_window():
    r = _gas_trend("s2-slow-oxidation", 48, "TP-1201-G1", "TEMP")
    assert r["change"] == round(r["latest"] - r["first"], 3) and r["change"] > 0, r
    assert r["rate_per_hour"] == round(r["rate_per_hour"], 3)  # 与其他输出一样保留 3 位，Agent 照抄不出长尾数
    assert r["first_ts"] < r["latest_ts"]


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


# ── 自燃发展阶段、处置效果评估（判据为占位值，这里只检查在标准场景上方向正确）──


def test_stage_follows_scenarios():
    for h in (12, 36, 60, 96):
        assert level("s1-normal", h).stage == "no_sign", h
    assert level("s2-slow-oxidation", 48).stage == "slow_oxidation"
    assert level("s2-slow-oxidation", 84).stage == "accelerating"  # 乙烯已检出
    stages = [level("s3-accelerating", h).stage for h in (10, 20, 30, 40)]
    order = ["no_sign", "slow_oxidation", "accelerating", "intense"]
    assert [order.index(s) for s in stages] == sorted(order.index(s) for s in stages), stages


def test_stage_unknown_without_tube_co():
    from rescue_fire_model.stage import classify_stage

    assert classify_stage({"tube_co_ppm": None, "c2h4_ppm": None}, warning.load_thresholds()["stage"])[0] == "unknown"


def _effect(act: bool, after_h: float, at_h: float = 48, measurand: str = "CO", sensor_id: str = "BT-1201-G1"):
    from mcp.client import Client
    from rescue_fire_model.server import build_server

    holder, _ = sim("s2-slow-oxidation", at_h)
    since = holder.engine.now().replace(microsecond=0)
    if act:
        holder.engine.apply("inert_injection", {"rate_m3_h": 1000})
        holder.engine.apply("leak_sealing", {"reduction": 0.5})
    holder.engine.clock.advance(after_h)

    async def go():
        async with Client(build_server(build_mine(holder))) as c:
            args = {"sensor_id": sensor_id, "since": since.isoformat(), "measurand": measurand}
            r = await c.call_tool("evaluate_effect", args)
            return r.structured_content if not r.is_error else r.content[0].text

    return asyncio.run(go())


def test_effect_needs_a_full_observation_period():
    r = _effect(True, 6)
    assert r["effect"] == "insufficient" and r["observed_h"] == 6.0, r


def test_s2_intervention_needs_continued_treatment():
    # 场景说明：第 2 天注氮加堵漏后升温基本被遏制，但仍需持续处置，不应判为已受控
    r = _effect(True, 24)
    assert r["effect"] == "continuing", r
    assert 0 < r["rate_after_per_hour"] < r["rate_before_per_hour"]


def test_s2_without_intervention_is_worsening():
    r = _effect(False, 24)
    assert r["effect"] == "worsening" and r["rate_after_per_hour"] > r["rate_before_per_hour"], r


def test_effect_controlled_and_new_marker_gas():
    from datetime import datetime, timedelta

    from rescue_contract.models import Measurand, Reading
    from rescue_fire_model.stage import evaluate

    t0 = datetime(2026, 11, 2, 8)
    cfg = warning.load_thresholds()

    def series(m, values, start):
        return [Reading(sensor_id="BT", measurand=m, ts=start + timedelta(hours=i), value=v) for i, v in enumerate(values)]

    before = series(Measurand.CO, [20, 22, 24, 26], t0 - timedelta(hours=3))
    falling = series(Measurand.CO, [26, 25, 24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14], t0)
    now = t0 + timedelta(hours=12)
    r = evaluate(t0, now, before, falling, {}, cfg["stage"], cfg["effect"])
    assert r["effect"] == "controlled" and r["change_since"] == -12, r

    zeros = series(Measurand.C2H4, [0] * 4, t0 - timedelta(hours=3))
    appears = series(Measurand.C2H4, [0] * 12 + [0.5], t0)
    r = evaluate(t0, now, before, falling, {"C2H4": (zeros, appears)}, cfg["stage"], cfg["effect"])
    assert r["effect"] == "worsening" and r["new_marker_gases"] == ["C2H4"], r


def test_effect_rejects_non_fire_indicators():
    # O2 下降、采空区 CH4 波动不说明火情好坏（评测中曾把 CH4 -0.018→0.002 %/h 判成“恶化”）
    for m in ("CH4", "O2"):
        msg = _effect(True, 24, measurand=m)
        assert isinstance(msg, str) and "不是发火指标" in msg, msg


def test_effect_temperature_follows_co():
    assert _effect(True, 24, measurand="TEMP", sensor_id="TP-1201-G1")["effect"] == "continuing"


def test_effect_noise_band_counts_as_flat():
    from datetime import datetime, timedelta

    from rescue_contract.models import Measurand, Reading
    from rescue_fire_model.stage import evaluate

    t0 = datetime(2026, 11, 2, 8)
    cfg = warning.load_thresholds()

    def series(values, start):
        return [Reading(sensor_id="BT", measurand=Measurand.CO, ts=start + timedelta(hours=i), value=v) for i, v in enumerate(values)]

    before = series([20.03, 20.0, 19.98, 19.95], t0 - timedelta(hours=3))  # 约 -0.025/h，在回落
    after = series([20 + 0.002 * i for i in range(13)], t0)  # 0.002/h，噪声
    r = evaluate(t0, t0 + timedelta(hours=12), before, after, {}, cfg["stage"], cfg["effect"], cfg["effect"]["flat_rate"]["CO"])
    assert r["effect"] == "controlled" and r["rate_after_per_hour"] == 0.002, r


def _panel_effect(act: bool, after_h: float) -> dict:
    from mcp.client import Client
    from rescue_fire_model.server import build_server

    holder, _ = sim("s2-slow-oxidation", 48)
    since = holder.engine.now().replace(microsecond=0)
    if act:
        holder.engine.apply("inert_injection", {"rate_m3_h": 1000})
        holder.engine.apply("leak_sealing", {"reduction": 0.5})
    holder.engine.clock.advance(after_h)

    async def go():
        async with Client(build_server(build_mine(holder))) as c:
            return (await c.call_tool("evaluate_effect_panel", {"since": since.isoformat()})).structured_content

    return asyncio.run(go())


def test_panel_effect_s2_with_and_without_treatment():
    from datetime import datetime, timedelta

    r = _panel_effect(True, 24)
    assert r["overall"] == "continuing", r
    # 发火指标测点：采空区束管 CO、采空区测温、非进风侧安全监控 CO（不含进风巷 SM-1201-IN）
    assert {(p["sensor_id"], p["measurand"]) for p in r["points"]} >= {("BT-1201-G1", "CO"), ("TP-1201-G1", "TEMP"), ("SM-1201-UC", "CO")}
    assert "SM-1201-IN" not in {p["sensor_id"] for p in r["points"]}
    assert sum(r["counts"].values()) == r["evaluated"] == len(r["points"])
    # 时长和复评时刻由工具给出（处置后 24 小时、复评间隔 12 小时）
    assert r["observed_h"] == 24.0 and r["observed_h_at_next_review"] == 36.0
    assert datetime.fromisoformat(r["next_review_at"]) - datetime.fromisoformat(r["as_of"]) == timedelta(hours=12)
    assert _panel_effect(False, 24)["overall"] == "worsening"
    assert _panel_effect(True, 6)["overall"] == "insufficient"


@pytest.mark.parametrize(
    ("effects", "expected"),
    [
        (["controlled", "controlled", "insufficient"], "controlled"),  # 观测不足的测点不参与
        (["controlled", "continuing"], "continuing"),
        (["controlled", "continuing", "worsening"], "worsening"),
        (["insufficient", "insufficient"], "insufficient"),
    ],
)
def test_panel_overall_rule(effects, expected):
    from datetime import datetime

    from rescue_fire_model.stage import EFFECT_NAMES, overall

    keys = ("value_at_since", "latest", "change_since", "rate_before_per_hour", "rate_after_per_hour", "rate_reduction_pct")
    points = [{"sensor_id": f"S{i}", "measurand": "CO", "effect": e, "effect_name": EFFECT_NAMES[e], "new_marker_gases": [], **dict.fromkeys(keys)} for i, e in enumerate(effects)]
    t = datetime(2026, 11, 4, 8)
    assert overall(points, t, t, 12)["overall"] == expected


# ── 爆炸三角形风险图 ──


@pytest.mark.parametrize(
    "gases,o2",
    [({"CH4": 0.042}, 19.8), ({"CH4": 4.5, "H2": 1.5}, 18.0), ({"CH4": 3.7, "CO": 0.3, "C2H4": 0.001}, 7.8), ({"CO": 30.0}, 10.0)],
)
def test_chart_zones_match_classifier(gases, o2):
    """图上的色块与判定逐点一致：网格上每个点所在色块 = classify 的分区。"""
    from rescue_fire_model import chart

    r = triangle(gases, o2)
    tri = tuple((r["triangle"][k]["combustible_pct"], r["triangle"][k]["o2_pct"]) for k in ("lower", "upper", "critical"))
    xmax = 100.0
    polys = chart.zone_polygons(tri, XC.air_o2, xmax)
    checked = 0
    for i in range(1, 200):
        for j in range(1, 60):
            p = (i * 0.5013, j * 0.3497)  # 避开整数格线，减少正好落在边界上的点
            if p[1] >= XC.air_o2 * (1 - p[0] / 100) - 1e-6:
                continue
            assert chart.drawn_zone(p, polys) == explosion.classify(p, tri, XC.air_o2)["zone"], p
            checked += 1
    assert checked > 5000


def test_chart_saved_and_downloadable(tmp_path, monkeypatch):
    from xml.etree import ElementTree

    from rescue_contract.serving import build_app
    from rescue_fire_model.server import build_server
    from starlette.testclient import TestClient

    monkeypatch.setenv("RESCUE_CHART_DIR", str(tmp_path))
    monkeypatch.setenv("RESCUE_MCP_TOKEN", "secret")
    holder, _ = sim("s4-escalation", 80)
    d = _call_fire(holder, "assess_explosion_risk", {"sensor_id": "BT-1201-G1"}).structured_content
    c = d["chart"]
    svg = tmp_path / c["svg_path"].rsplit("/", 1)[1]
    ElementTree.fromstring(svg.read_text(encoding="utf-8"))  # 合法 XML
    assert f"{d['zone']} 区" in svg.read_text(encoding="utf-8")
    assert c["markdown"].startswith(f"![火区混合气体爆炸三角形]({c['svg_path']})")

    app = build_app(build_server(build_mine(holder)), public_prefixes=("/charts/",))
    with TestClient(app) as http:
        path = "/charts/" + svg.name
        r = http.get(path)  # 不带令牌也能下载
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
        assert "attachment" in r.headers["content-disposition"]
        assert http.get("/charts/explosion-triangle-x-" + "0" * 32 + ".svg").status_code == 404
        assert http.get("/charts/..%2Fsecret.svg").status_code == 404  # 文件名不合规即拒绝
        assert http.post("/mcp", json={}).status_code == 401  # 其他路径仍要令牌
