"""演示模式（design-v2 4.10）：常态连续运行、事故注入、读数设置、自动暂停。"""

import json
import time

import pytest
from rescue_contract.incidents import IncidentStore
from rescue_contract.models import Measurand, Quality
from rescue_mine_data_sim.engine import SimEngine
from rescue_mine_data_sim.scenarios import load_all
from rescue_mine_data_sim.server import IncidentWatcher, RecordWatcher, SimHolder, _sentinel_status

SC = load_all()


def normal(manual=True):
    e = SimEngine(SC["s1-normal"], manual_clock=manual, continuous=True)
    return e


def test_continuous_has_no_end():
    e = normal()
    e.clock.advance(SC["s1-normal"].duration_h + 100)
    assert e.now_h() == pytest.approx(SC["s1-normal"].duration_h + 100)


def test_inject_keeps_history_and_shifts_script():
    ref = normal()
    e = normal()
    for x in (ref, e):
        x.clock.advance(24)
    before = [r.value for r in e.readings(["BT-1201-G1"], 0, 24, [Measurand.CO])]
    rec = e.inject(SC["s4-escalation"])
    assert rec["at_h"] == pytest.approx(24) and rec["scenario"] == "s4-escalation"
    # 注入之前的读数不变；之后过程参数换成事故场景的，温度上升得比常态快
    assert [r.value for r in e.readings(["BT-1201-G1"], 0, 24, [Measurand.CO])] == before
    for x in (ref, e):
        x.clock.advance(40)
    assert e.hotspot()["temperature_c"] > ref.hotspot()["temperature_c"] + 20
    # 剧本事件按注入时刻平移：s4 的 leak_change 在第 30 小时 → 注入后第 30 小时
    leak = [iv for iv in e._interventions if iv.type == "leak_change"]
    assert leak and leak[0].at_h == pytest.approx(24 + 30)


def test_inject_matches_scenario_from_its_start():
    """s1 与 s4 的初始温度相同：在常态第 0 小时注入，与直接运行 s4 一致。"""
    e = normal()
    e.inject(SC["s4-escalation"])
    direct = SimEngine(SC["s4-escalation"], manual_clock=True)
    for x in (e, direct):
        x.clock.advance(45)
    assert e.hotspot()["temperature_c"] == direct.hotspot()["temperature_c"]


def test_overrides_only_after_set_and_follow_restores():
    e = normal()
    e.clock.advance(10)
    hist = [r.value for r in e.readings(["SM-1201-UC"], 0, 10, [Measurand.CO])]
    e.set_override("SM-1201-UC", Measurand.CO, "fixed", 50)
    e.clock.advance(1)
    r = e.latest(["SM-1201-UC"])
    assert next(x for x in r if x.measurand == Measurand.CO).value == 50
    assert next(x for x in r if x.measurand == Measurand.CH4).value != 50  # 只改这个参数
    assert [r.value for r in e.readings(["SM-1201-UC"], 0, 10, [Measurand.CO])] == hist  # 历史不变
    pts = e.series("SM-1201-UC", Measurand.CO, 10, 11)
    assert pts[-1]["output"] == 50 and pts[-1]["manual"] and pts[-1]["model"] != 50
    e.set_override("SM-1201-UC", Measurand.CO, "follow")
    e.clock.advance(1)
    assert next(x for x in e.latest(["SM-1201-UC"]) if x.measurand == Measurand.CO).value != 50
    assert e.series("SM-1201-UC", Measurand.CO, 10.5, 11)[-1]["output"] == 50  # 设置期间的读数保留


def test_override_modes():
    e = normal()
    e.clock.advance(5)
    e.set_override("SM-1201-RET", Measurand.CO, "ramp", 100, minutes=60)
    e.clock.advance(0.5)
    mid = e.series("SM-1201-RET", Measurand.CO, 5.5, 5.5)[0]
    assert mid["model"] < mid["output"] < 100
    e.clock.advance(1)
    assert e.series("SM-1201-RET", Measurand.CO, 6.5, 6.5)[0]["output"] == 100
    e.set_override("BT-1201-G1", None, "fault", fault="dropout")
    e.clock.advance(2)
    assert all(r.quality == Quality.MISSING for r in e.latest(["BT-1201-G1"]))
    e.set_override("SM-1201-IN", Measurand.CO, "offset", 3)
    e.clock.advance(0.25)
    p = e.series("SM-1201-IN", Measurand.CO, e.now_h(), e.now_h())[0]
    assert p["output"] == pytest.approx(p["model"] + 3, abs=1e-3)
    with pytest.raises(ValueError):
        e.set_override("SM-1201-IN", Measurand.CO, "ramp", 5)
    with pytest.raises(ValueError):
        e.set_override("NO-SUCH", None, "fixed", 1)


def test_record_watcher_pauses_on_anomaly(tmp_path):
    sim = SimHolder(mode="demo")
    sim.resumed_wall = time.time() - 1
    w = RecordWatcher(sim, tmp_path)

    def put(name, skill, script, anomaly):
        content = {"result": {"anomaly": anomaly, "reasons": ["预警等级由 无 升为 蓝色（关注）"]}}
        (tmp_path / f"{name}.json").write_text(json.dumps({"record_id": name, "panel": "1201", "as_of": "2026-11-02T10:00:00", "source": {"skill": skill, "script": script}, "content": content, "origin": {"trigger": "watch"}}), encoding="utf-8")

    put("RS-20261102T100000-aaaaaa", "fire-assessment", "check", False)
    put("RS-20261102T100000-bbbbbb", "fire-assessment", "warning", True)
    assert w.scan() == []
    put("RS-20261102T100000-cccccc", "fire-assessment", "check", True)
    hits = w.scan()
    assert [h["record_id"] for h in hits] == ["RS-20261102T100000-cccccc"] and hits[0]["trigger"] == "watch"
    assert w.scan() == []  # 已看过的不再报告


def test_incident_watcher_and_sentinel_status(tmp_path):
    store = IncidentStore(tmp_path)
    iid = store.open("2026-11-05T08:00:00", category="fire", panel="1201", summary="x")
    store.append(iid, "2026-11-05T08:01:00", "alert", "sentinel", {"summary": "启动前就有的告警"})
    sim = SimHolder(mode="demo")
    w = IncidentWatcher(sim, store)
    assert w.scan() == []  # 第一次只登记已有的告警，不触发暂停
    sim.resumed_wall = time.time() - 1
    store.append(iid, "2026-11-05T08:10:00", "alert", "sentinel", {"summary": "预警等级由 无 升为 蓝"})
    store.append(iid, "2026-11-05T08:11:00", "delivered", "plugin", {"alert_id": "x"})
    hits = w.scan()
    assert [(h["trigger"], h["reasons"], h["panel"]) for h in hits] == [("sentinel", ["预警等级由 无 升为 蓝"], "1201")]
    assert w.scan() == []
    # 心跳：没有 = 未启动；新鲜 = 监听中；带错误 = 出错；过期 = 监听中断
    assert _sentinel_status(tmp_path)["state"] == "absent"
    (tmp_path / "sentinel").mkdir()
    hb = tmp_path / "sentinel" / "heartbeat.json"
    hb.write_text(json.dumps({"wall": time.time(), "mine_now": "t", "error": None, "panels": ["1201"]}))
    assert _sentinel_status(tmp_path)["state"] == "ok"
    hb.write_text(json.dumps({"wall": time.time(), "error": "连接失败"}))
    assert _sentinel_status(tmp_path)["state"] == "error"
    hb.write_text(json.dumps({"wall": time.time() - 120}))
    assert _sentinel_status(tmp_path)["state"] == "stale"


def test_demo_routes():
    """/sim/demo/*、/sim/overrides、/sim/series、/console 经 HTTP（不设令牌）。"""
    from rescue_contract.serving import build_app
    from rescue_mine_data_sim.server import build_server
    from starlette.testclient import TestClient

    holder = SimHolder("s1-normal", manual_clock=True)
    c = TestClient(build_app(build_server(holder), public_prefixes=("/console",)))
    assert c.post("/sim/demo/inject", json={"scenario": "s4-escalation"}).status_code == 400  # 不在常态连续运行
    assert c.post("/sim/demo/normal", json={"speed": 60}).json()["ok"]
    holder.engine.clock.set_paused(True)
    holder.engine.clock.advance(2)
    r = c.post("/sim/demo/inject", json={"scenario": "s4-escalation", "speed": 600}).json()
    assert r["injected"]["scenario"] == "s4-escalation" and r["speed"] == 600
    st = c.get("/sim/state").json()
    assert st["mode"] == "demo" and st["injected"] and [x["type"] for x in st["script"]] == ["stop_advance", "leak_change"]
    assert c.post("/sim/overrides", json={"sensor_id": "SM-1201-UC", "measurand": "CO", "mode": "fixed", "value": 40}).json()["active"]
    assert c.post("/sim/overrides", json={"sensor_id": "SM-1201-UC", "mode": "ramp", "value": 1}).status_code == 400
    holder.engine.clock.advance(1)
    pts = c.get("/sim/series", params={"sensor_id": "SM-1201-UC", "measurand": "CO", "hours": 1}).json()["points"]
    assert pts[-1]["output"] == 40 and pts[-1]["manual"]
    assert c.get("/sim/state").json()["overrides"][0]["value"] == 40
    assert "演示控制台" in c.get("/console").text
