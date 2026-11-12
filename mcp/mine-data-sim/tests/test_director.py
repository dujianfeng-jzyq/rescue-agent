"""演示导演（第五版）：按等级告警推进、暂停等选择、按所选个数走分支、走完结束。监听器用一个假的代替（按矿井时间写告警和心跳）。"""

import json
from pathlib import Path

import pytest
from rescue_mine_data_sim.director import Director, alert_level, level_of, load_flow, validate_flow
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
    d = Director(sim, FLOW, store, hb, wait_s=0.0, settle_s=0.0)
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


def test_alert_level_prefers_the_structured_field():
    """监听器写了 level_to 就按它，不看摘要文字（措辞改了也认得出）；没有时退回解析文字。"""
    assert alert_level({"level_to": "orange", "summary": "措辞改了：等级上升"}) == "橙色"
    assert alert_level({"level_to": "blue", "summary": "预警等级由 无 升为 蓝色（关注）"}) is None  # 蓝色不在演示流程里
    assert alert_level({"summary": "预警等级由 黄色（预警） 升为 橙色（严重）"}) == "橙色"
    assert alert_level({"summary": "新的实时报警"}) is None


def test_flow_valid_and_rejects_bad():
    bad = {"incident": "x", "stages": {"橙色": {"options": [{"id": "a"}], "branches": [{"goto": "不存在"}]}}}  # 选项不在流程文件里写
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


def guard_executes(sim, *types):
    """模拟 guard 经 rescue-mine 下发处置：矿井处置日志里来源是 action。"""
    for t in types:
        sim.engine.apply(t, {}, [], "action")


def test_red_waits_for_both_evacuation_and_seal(tmp_path):
    """红色：撤人和封闭由智能体按流程落实，两项都执行了才结束；只做了一项继续等。"""
    sim, store, hb, d = make(tmp_path)
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5, "红色": 60})
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["monitor"])
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["inert"])  # 只选 1 项 → 红色
    drive(d, fake, lambda: d.state == "holding" and d.stage == "红色")
    guard_executes(sim, "evacuate")
    d.tick()
    assert d.state == "holding" and d.stage == "红色"  # 只撤人，还要等封闭
    guard_executes(sim, "temporary_seal", "permanent_seal")
    d.tick()
    assert d.state == "ending" and "封闭" in d.status()["outcome"]


def test_director_recognizes_what_the_guard_executed(tmp_path):
    """没有控制台点选：操作员在智能体的卡片上选、批准下发后，矿井执行了处置，导演据此认出选项并走分支。"""
    sim, store, hb, d = make(tmp_path)
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5})
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    d.tick()
    assert d.state == "holding"  # 没有处置就一直等
    guard_executes(sim, "monitor")
    d.tick()
    assert d.state == "running" and d.stage == "橙色"
    drive(d, fake, lambda: d.state == "holding")
    # 防灭火方案下发：检测观测、注氮、注浆（2 项）→ 效果有限，仍停在橙色；monitor 不属于橙色选项，不计
    guard_executes(sim, "monitor", "inert_injection", "grouting")
    d.tick()
    assert d.state == "holding" and d.stage == "橙色"
    assert [o["id"] for o in d.status()["options"] if o["done"]] == ["inert", "grout"]
    guard_executes(sim, "leak_sealing")  # 追加堵漏，累计 3 项
    d.tick()
    assert d.state == "ending"
    assert not any(iv.source == "demo-operator" for iv in sim.engine.action_log)  # 导演没有自己施加


def test_operator_declines_goes_to_red_and_skip_keeps_waiting(tmp_path):
    """2026-11-11：卡片上输入“不采取处置”（智能体记进时间线，矿井什么也不执行）→ 走 declined 分支到红色；点跳过（暂不决定）或选择冲突 → 继续等。"""
    sim, store, hb, d = make(tmp_path)
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5, "红色": 50})
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["monitor"])
    drive(d, fake, lambda: d.state == "holding")
    assert d.stage == "橙色"
    at = sim.engine.now().isoformat(timespec="seconds")
    store.append(fake.iid, at, "note", "flow", {"type": "response_choice", "level": "orange", "choice": "undecided", "option_ids": []})
    d.tick()
    assert d.state == "holding" and d.stage == "橙色"  # 暂不决定：继续等
    assert "暂未选择" in d.status()["log"][-1]["text"]
    store.append(fake.iid, at, "note", "flow", {"type": "response_choice", "level": "orange", "choice": "conflict", "option_ids": ["grout"]})
    d.tick()
    assert d.state == "holding" and d.stage == "橙色"  # 冲突：继续等
    assert "冲突" in d.status()["log"][-1]["text"]
    store.append(fake.iid, at, "note", "flow", {"type": "response_choice", "level": "orange", "choice": "none", "option_ids": []})
    d.tick()
    assert d.state == "running" and d.stage == "红色"
    assert d.chosen[-1] == {"stage": "橙色", "options": [], "declined": True}
    assert any(iv.type == "leak_change" and iv.source == "demo-script" for iv in sim.engine.action_log)


def test_choice_recorded_before_the_hold_is_ignored(tmp_path):
    """进入等待之前记下的选择（上一轮的）不算。"""
    sim, store, hb, d = make(tmp_path)
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5})
    store.append(fake.iid, "2026-11-02T08:00:00", "note", "flow", {"type": "response_choice", "level": "orange", "choice": "none", "option_ids": []})
    import time as _t

    _t.sleep(1.1)  # 事件时间戳截到秒
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["monitor"])
    drive(d, fake, lambda: d.state == "holding")
    d.tick()
    assert d.state == "holding" and d.stage == "橙色"


def test_declined_branch_cannot_be_again():
    import copy

    bad = copy.deepcopy(FLOW)
    bad["stages"]["橙色"]["branches"].append({"when": {"declined": True}, "again": True})
    with pytest.raises(ValueError, match="不采取处置"):
        validate_flow(bad)


def test_old_choosable_actions_before_the_hold_are_ignored(tmp_path):
    """可选项只认进入等待之后执行的：橙色之前就做的注浆不算橙色的选择。"""
    sim, store, hb, d = make(tmp_path)
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5})
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["monitor"])
    guard_executes(sim, "grouting")
    drive(d, fake, lambda: d.state == "holding")
    d.tick()
    assert d.state == "holding" and d.stage == "橙色"


def test_mandatory_action_already_in_effect_counts(tmp_path):
    """蓝色时智能体已开了加密监测，到黄色不再重复下发：黄色的必做项算已到位，不卡住。"""
    sim, store, hb, d = make(tmp_path)
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5})
    sim.engine.apply("monitor", {}, [], "action")  # 演示开始之前的不算
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    d.tick()
    assert d.state == "holding" and d.stage == "黄色" and not d._matched
    d.reset()
    d.start()
    guard_executes(sim, "monitor")  # 蓝色阶段（等黄色之前）下发
    drive(d, fake, lambda: d.state == "holding")
    assert any("已在之前下发" in e["text"] for e in d.log)
    d._last_match -= d.settle_s + 1
    d.tick()
    assert d.state == "running" and d.stage == "橙色"


def test_settle_waits_for_the_whole_package(tmp_path):
    sim, store, hb, d = make(tmp_path)
    d.settle_s = 60.0
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5})
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    guard_executes(sim, "monitor")
    d.tick()
    assert d.state == "holding"  # 静默期没过，还在等同一批处置的其余部分
    d._last_match -= 61
    d.tick()
    assert d.state == "running"


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
    # 网页端用的演示进度：只有状态和结局，不含真值
    assert c.get("/demo/status").json() == {"state": "running", "outcome": "", "ended_at": None}


def test_normal_reset_resets_the_director():
    from rescue_contract.serving import build_app
    from rescue_mine_data_sim.server import build_server
    from starlette.testclient import TestClient

    c = TestClient(build_app(build_server(SimHolder(mode="demo")), public_prefixes=("/console",)))
    assert c.post("/sim/demo/start").json()["state"] == "running"
    assert c.post("/sim/demo/normal", json={"speed": 60}).json()["ok"]
    assert c.get("/sim/demo/director").json()["state"] == "idle"  # 重置为常态后可以再开始
    assert c.post("/sim/demo/start").json()["state"] == "running"


def test_jump_stops_at_a_pending_followup(tmp_path):
    """红色的大跳（jump_h）途中有跟踪计划到点：先停在到点时刻（等智能体复评），再跳剩下的，总共跳的不变。"""
    sim, store, hb, d = make(tmp_path)
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5, "红色": 70})
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["monitor"])
    drive(d, fake, lambda: d.state == "holding")
    d.choose(["inert"])  # → 红色，jump_h 20
    t0 = sim.engine.now_h()
    due = sim.engine.now().replace(tzinfo=None, microsecond=0)
    from datetime import timedelta

    due_at = (due + timedelta(hours=2)).isoformat(timespec="seconds")
    store.append(fake.iid, due.isoformat(timespec="seconds"), "followup_added", "agent", {"followup_id": "FU-x", "due_at": due_at, "expect": "x"})
    d.tick()
    assert abs(sim.engine.now_h() - (t0 + 2)) < 1e-6
    assert "FU-x" in d.status()["log"][-1]["text"]
    store.append(fake.iid, due_at, "followup_due", "sentinel", {"followup_id": "FU-x"})  # 监听器写到点（之后由智能体复评）
    fake.poll()
    d.tick()  # 等监听器检测完
    d.tick()  # 跳剩下的 18 小时
    assert abs(sim.engine.now_h() - (t0 + 20)) < 1e-6
    assert "接近红色预警" in d.status()["log"][-1]["text"]


def test_waits_for_the_duty_agent_before_advancing(tmp_path):
    """值班智能体还在处理（会话忙，或有告警没投递）时导演不推进矿井时间；状态文件过期（没接 DSH）不等。"""
    import time

    sim, store, hb, d = make(tmp_path)
    duty = tmp_path / "inc" / "duty-status.json"
    d.duty = duty
    fake = FakeSentinel(sim, store, hb, {"黄色": 35.5, "橙色": 37.5})

    def status(busy: bool, age_s: float = 0.0) -> None:
        duty.write_text(json.dumps({"busy": busy, "wall": time.time() - age_s}))

    status(False)
    d.start()
    drive(d, fake, lambda: d.state == "holding")
    for a in store.pending_alerts():  # 黄色告警已投递给值班会话
        store.append(a["incident_id"], a["at"], "delivered", "plugin", {"alert_id": a["event_id"], "session_id": "s"})
    guard_executes(sim, "monitor")
    d.tick()
    assert d.state == "running" and d.stage == "橙色"
    status(True)  # 智能体还在这一轮里（登记跟踪、写报告）
    t = sim.engine.now_h()
    for _ in range(5):
        d.tick()
        fake.poll()
    assert sim.engine.now_h() == t and d.status()["waiting"] == "等值班智能体处理完"
    status(True, age_s=60)  # DSH 停了：状态过期，不等（橙色 jump_h 为 0，第一步不动，多推几步）
    for _ in range(4):
        d.tick()
        fake.poll()
    assert sim.engine.now_h() > t
    status(False)
    drive(d, fake, lambda: bool(store.pending_alerts()))
    assert d._agent_busy() == "等告警投递给值班智能体"  # 新告警还没投递（唤醒插件下一次轮询才投）：不算空闲


def test_manual_controls_are_refused_while_directing():
    from rescue_contract.serving import build_app
    from rescue_mine_data_sim.server import build_server
    from starlette.testclient import TestClient

    c = TestClient(build_app(build_server(SimHolder(mode="demo")), public_prefixes=("/console",)))
    assert c.post("/sim/demo/start").json()["state"] == "running"
    assert c.post("/sim/demo/inject", json={"scenario": "s4-escalation"}).status_code == 409
    assert c.post("/sim/demo/resume", json={}).status_code == 409
    assert c.post("/sim/advance", json={"hours": 1}).status_code == 409
    assert c.post("/sim/pause", json={"paused": False}).status_code == 409
    assert c.post("/sim/pause", json={"paused": True}).json()["ok"]
