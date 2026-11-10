"""演示导演（第五版）：按等级告警推进、暂停等选择、按所选个数走分支、走完结束。监听器用一个假的代替（按矿井时间写告警和心跳）。"""

import json
from pathlib import Path

import pytest
from rescue_mine_data_sim.director import Director, level_of, load_flow, validate_flow
from rescue_mine_data_sim.scenarios import scenario_dir
from rescue_mine_data_sim.server import SimHolder

FLOW = load_flow(scenario_dir() / "demo" / "flow.yaml")


class FakeSentinel:
    """矿井时间到了某个时刻就写一条等级告警；每次都写心跳（矿井时间 = 当前时钟）。"""

    def __init__(self, sim, store, hb: Path, at_h: dict[str, float]) -> None:
        self.sim, self.store, self.hb, self.at_h, self.done = sim, store, hb, at_h, set()
        self.iid = store.open("2026-11-02T08:00:00", category="fire", panel="1201", summary="x")
        self.base = 0.0

    def poll(self) -> None:
        e = self.sim.engine
        now = e.now()
        for level, h in self.at_h.items():
            if level not in self.done and e.now_h() - self.base >= h:
                self.done.add(level)
                self.store.append(self.iid, now.isoformat(timespec="seconds"), "alert", "sentinel", {"summary": f"1201 工作面：预警等级由 无 升为 {level}（预警）"})
        self.hb.parent.mkdir(parents=True, exist_ok=True)
        self.hb.write_text(json.dumps({"wall": 0, "mine_now": now.isoformat(timespec="seconds"), "error": None}))


def make(tmp_path):
    from rescue_contract.incidents import IncidentStore

    sim = SimHolder(mode="demo")
    store = IncidentStore(tmp_path / "inc")
    hb = tmp_path / "inc" / "sentinel" / "heartbeat.json"
    d = Director(sim, FLOW, store, hb, wait_s=0.0)
    return sim, store, hb, d


def drive(d, fake, until, limit=400):
    for _ in range(limit):
        d.tick()
        fake.poll()
        if until():
            return
    raise AssertionError(f"没有走到目标，状态 {d.status()}")


def test_level_of():
    assert level_of("211016 工作面：预警等级由 无 升为 黄色（预警）") == "黄色"
    assert level_of("预警等级由 橙色（严重） 升为 红色（危险）") == "红色"
    assert level_of("监听降级") is None


def test_flow_valid_and_rejects_bad():
    bad = {"incident": "x", "stages": {"橙色": {"options": [{"id": "a"}], "branches": [{"goto": "不存在"}]}}}
    with pytest.raises(ValueError):
        validate_flow(bad)


def test_full_story_three_options_recede(tmp_path):
    sim, store, hb, d = make(tmp_path)
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5})
    d.start()
    assert sim.engine.clock.paused and d.state == "running"
    drive(d, fake, lambda: d.state == "holding")
    assert d.stage == "黄色" and sim.engine.clock.paused
    assert 35.0 <= sim.engine.now_h() <= 36.5  # 先跳 33 小时再按 0.5 小时一步，不冲过
    t_hold = sim.engine.now_h()
    for _ in range(5):  # 暂停期间时间不动
        d.tick()
    assert sim.engine.now_h() == t_hold
    d.choose(["monitor"])
    drive(d, fake, lambda: d.state == "holding")
    assert d.stage == "橙色" and sim.engine.now_h() < 38.6
    d.choose(["inert", "grout", "leak_seal"])
    assert d.state == "ending"
    drive(d, fake, lambda: d.state == "finished")
    assert sim.engine.clock.paused and sim.engine.now_h() > t_hold + 34  # 结束段跑了 36 矿井小时
    assert [c["options"] for c in d.chosen] == [["monitor"], ["inert", "grout", "leak_seal"]]
    # 所选操作进了矿井的处置日志（过渡期由导演施加），来源是演示操作员
    assert {iv.type for iv in sim.engine.action_log if iv.source == "demo-operator"} >= {"monitor", "inert_injection", "grouting", "leak_sealing"}


def test_one_option_goes_to_red_then_ends(tmp_path):
    sim, store, hb, d = make(tmp_path)
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5, "红色": 50})
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["monitor"])
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["inert"])  # 只选 1 项 → 红色分支，注入漏风突增
    assert d.state == "running" and d.stage == "红色"
    assert any(iv.type == "leak_change" and iv.source == "demo-script" for iv in sim.engine.action_log)
    drive(d, fake, lambda: d.state == "holding")
    assert d.stage == "红色"
    d.choose(["evacuate", "seal"])
    drive(d, fake, lambda: d.state == "finished")
    assert "封闭" in d.status()["outcome"]


def test_two_options_ask_for_more_then_recede(tmp_path):
    sim, store, hb, d = make(tmp_path)
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5})
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["monitor"])
    drive(d, fake, lambda: d.state == "holding")
    t = sim.engine.now_h()
    d.choose(["inert", "grout"])  # 2 项：效果有限，仍停在橙色，时间不动
    assert d.state == "holding" and d.stage == "橙色" and sim.engine.now_h() == t
    assert [o["id"] for o in d.status()["options"] if o["done"]] == ["inert", "grout"]
    with pytest.raises(ValueError):
        d.choose(["inert"])  # 已下发的不能重复
    d.choose(["leak_seal"])  # 累计 3 项 → 回落
    assert d.state == "ending"
    assert sum(1 for iv in sim.engine.action_log if iv.source == "demo-operator" and iv.type == "inert_injection") == 1


def test_choose_validation(tmp_path):
    sim, store, hb, d = make(tmp_path)
    with pytest.raises(ValueError):
        d.choose(["monitor"])  # 不在等待选择
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5})
    d.start()
    with pytest.raises(ValueError):
        d.start()  # 正在进行
    drive(d, fake, lambda: d.state == "holding")
    with pytest.raises(ValueError):
        d.choose(["不存在"])
    assert d.state == "holding"


def test_restart_resets_to_normal(tmp_path):
    sim, store, hb, d = make(tmp_path)
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5})
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["monitor"])
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["inert", "grout", "leak_seal"])
    drive(d, fake, lambda: d.state == "finished")
    d.start()  # 走完后可以再来一次：回到常态重新注入
    assert d.state == "running" and len(sim.engine.injected) == 1 and sim.engine.now_h() == pytest.approx(0, abs=0.05)


def test_director_routes():
    from rescue_contract.serving import build_app
    from rescue_mine_data_sim.server import build_server
    from starlette.testclient import TestClient

    holder = SimHolder(mode="demo")
    c = TestClient(build_app(build_server(holder), public_prefixes=("/console",)))
    assert c.get("/sim/demo/director").json()["state"] == "idle"
    assert c.post("/sim/demo/choose", json={"options": ["monitor"]}).status_code == 400  # 没在等待选择
    r = c.post("/sim/demo/start").json()
    assert r["ok"] and r["state"] == "running"
    assert c.post("/sim/demo/start").status_code == 400
    st = c.get("/sim/state").json()
    assert st["director"]["state"] == "running" and st["paused"] is True and st["injected"]
    assert "directorBox" in c.get("/console").text


def test_normal_reset_resets_the_director():
    from rescue_contract.serving import build_app
    from rescue_mine_data_sim.server import build_server
    from starlette.testclient import TestClient

    c = TestClient(build_app(build_server(SimHolder(mode="demo")), public_prefixes=("/console",)))
    assert c.post("/sim/demo/start").json()["state"] == "running"
    assert c.post("/sim/demo/normal", json={"speed": 60}).json()["ok"]
    assert c.get("/sim/demo/director").json()["state"] == "idle"  # 重置为常态后可以再开始
    assert c.post("/sim/demo/start").json()["state"] == "running"
