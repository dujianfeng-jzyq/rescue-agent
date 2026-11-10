"""第三版 P3 验收：监听器（不经模型）的期望——场景驱动的告警时刻、去重、滞回、降级、跟踪计划到点、重启恢复。

场景驱动的用例用进程内的模拟矿井（手动时钟，每个检测间隔推进一次）和真实的技能脚本；
状态机的用例（去重、恢复、降级、重启）替换掉取数和检测，只验证监听器自己的编排。
"""

from __future__ import annotations

import asyncio
import functools
from pathlib import Path

import pytest
import yaml
from rescue_contract.client import connect
from rescue_contract.collect import mcp_caller
from rescue_contract.incidents import IncidentStore
from rescue_mine_data_sim.server import SimHolder, build_server
from rescue_sentinel import core
from rescue_sentinel.core import Sentinel

ROOT = Path(__file__).resolve().parents[3]
EXPECT = yaml.safe_load((ROOT / "eval" / "expectations.yaml").read_text(encoding="utf-8"))
INTERVAL_H = 10 / 60


def arun(fn):
    """没有 pytest 异步插件：把 async 测试包成同步。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


async def drive(scenario: str, store: IncidentStore, hours: float, stop_at_alert: bool, stop_when=None) -> tuple[list[dict], float]:
    holder = SimHolder(scenario, manual_clock=True)
    alerts: list[dict] = []
    async with connect(build_server(holder)) as client:
        s = Sentinel(mcp_caller(client), store)
        t = 0.0
        while t <= hours + 1e-9:
            got = await s.tick()
            alerts += got
            if got and (stop_at_alert or (stop_when and any(stop_when(a) for a in got))):
                return alerts, t
            holder.engine.clock.advance(INTERVAL_H)
            t += INTERVAL_H
    return alerts, t


@arun
async def test_nominal_scenario_has_no_alert(tmp_path):
    store = IncidentStore(tmp_path)
    alerts, _ = await drive("s1-normal", store, 30, stop_at_alert=False)
    assert alerts == [] and store.list() == [], [a["data"] for a in alerts]
    assert (tmp_path / "sentinel" / "heartbeat.json").is_file()


@pytest.mark.parametrize("scenario", [k for k, v in EXPECT.items() if isinstance(v, dict) and v.get("watch", {}).get("end_reason") == "anomaly"])
@arun
async def test_alert_by_expected_time(scenario, tmp_path):
    exp = EXPECT[scenario]["watch"]
    store = IncidentStore(tmp_path)
    words = exp.get("reasons_any") or []
    # 期限内可能先有别的合法告警（如尖峰故障触发矿井报警）；要求命中预期原因的告警按时出现
    alerts, t = await drive(scenario, store, exp["end_by_h"] + 1, False, stop_when=lambda a: not words or any(w in a["data"]["summary"] for w in words))
    assert alerts, f"{exp['end_by_h'] + 1} 小时内没有告警"
    # 数据类异常要连续确认 confirm_checks 次，最多晚 (confirm_checks-1) 个检测间隔
    slack = (core_cfg_confirm() - 1) * INTERVAL_H
    assert t <= exp["end_by_h"] + slack + 1e-6, (t, [a["data"]["summary"] for a in alerts])
    assert not words or any(w in alerts[-1]["data"]["summary"] for w in words), alerts[-1]["data"]["summary"]
    (inc,) = store.list()
    assert inc["category"] == "fire" and inc["panel"] == "1201" and inc["pending_alerts"] == [a["event_id"] for a in alerts]


def core_cfg_confirm() -> int:
    return yaml.safe_load((ROOT / "skills/fire-assessment/references/watch.yaml").read_text(encoding="utf-8"))["sentinel"]["confirm_checks"]


# ── 状态机：替换取数和检测 ────────────────────────────────────────────────


def result(level="none", stage="no_sign", alarms=(), failed=(), stale=(), anomaly=None, escalated=False, reasons=None, now="2026-01-01T00:00:00"):
    state = {"level": level, "stage": stage, "alarm_keys": sorted(alarms), "failed": sorted(failed), "missing_groups": [], "stale": sorted(stale), "nominal": False}
    if anomaly is None:
        anomaly = bool(reasons)
    return {
        "panel": "1201", "as_of": now, "level": level, "level_name": level, "stage": stage, "stage_name": stage, "alarm_count": len(alarms),
        "anomaly": anomaly, "escalated": escalated, "reasons": reasons or [], "state": state,
    }  # fmt: skip


class Fake:
    """按脚本依次给出检测结果（或抛错）和矿井时钟。"""

    def __init__(self, tmp_path: Path, monkeypatch):
        async def fake_collect(call, spec, kind, panel, since=None):
            return {"kind": kind, "panel": panel}

        monkeypatch.setattr(core, "collect", fake_collect)
        self.now_min = 0
        self.script: list = []
        self.alarms: list = []
        self.followup_calls = 0
        self.store = IncidentStore(tmp_path)
        self.tmp = tmp_path
        self.s = self.new_sentinel()

    def new_sentinel(self) -> Sentinel:
        s = Sentinel(self.call, self.store)
        s._check = self._check  # type: ignore[method-assign]
        return s

    def mine_now(self) -> str:
        return f"2026-01-01T{self.now_min // 60:02d}:{self.now_min % 60:02d}:00"

    async def call(self, tool, args):
        if tool == "get_clock":
            return {"now": self.mine_now()}
        if tool == "get_alarms":
            return {"result": self.alarms}
        if tool == "list_sensors":
            return {"result": [{"id": "SM-1201-UC", "panel": "1201"}]}
        raise AssertionError(tool)

    def _check(self, ev, baseline):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return {**item, "as_of": self.mine_now()}

    async def step(self, item=None, minutes=10):
        if item is not None:
            self.script.append(item)
        self.now_min += minutes
        return await self.s.tick()


@arun
async def test_dedup_escalation_and_recovery(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    assert await f.step(result()) == []
    a1 = await f.step(result("blue", "latent", reasons=["预警等级由 无 升为 蓝"], escalated=True))
    assert len(a1) == 1 and a1[0]["data"]["cause"] == "anomaly"
    # 同一异常持续存在：不重复告警（基线 = 上次已上报）
    for _ in range(4):
        assert await f.step(result("blue", "latent")) == []
    a2 = await f.step(result("orange", "developing", reasons=["预警等级由 蓝 升为 橙"], escalated=True))
    assert len(a2) == 1
    (inc,) = f.store.list()
    assert len(inc["pending_alerts"]) == 2  # 两条告警并入同一个事件
    # 好转：连续 recover_checks 次不比基线更糟，基线下调；之后再升到橙色应再告警
    for _ in range(3):
        assert await f.step(result("blue", "latent")) == []
    a3 = await f.step(result("orange", "developing", reasons=["预警等级由 蓝 升为 橙"], escalated=True))
    assert len(a3) == 1
    # 回到正常：恢复、事件转已稳定；再次异常时回到监测中
    for _ in range(3):
        await f.step(result())
    inc = f.store.get(inc["incident_id"])
    assert inc["status"] == "stable"
    a4 = await f.step(result("blue", "latent", reasons=["预警等级由 无 升为 蓝"], escalated=True))
    assert len(a4) == 1 and f.store.get(inc["incident_id"])["status"] == "monitoring"
    assert len(f.store.list()) == 1


@arun
async def test_data_anomaly_needs_confirmation(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    glitch = result(failed=["BT-1201-G2/CO"], reasons=["必需测点失效：BT-1201-G2/CO"])
    assert await f.step(glitch) == []  # 第一次：只记连续次数
    assert await f.step(result()) == []  # 毛刺消失，计数清零
    assert await f.step(glitch) == []
    got = await f.step(glitch)
    assert len(got) == 1 and "必需测点失效" in got[0]["data"]["summary"]
    assert await f.step(result(failed=["BT-1201-G2/CO"])) == []  # 持续存在不重复


@arun
async def test_degraded_and_recovered(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    await f.step(result())
    assert await f.step(RuntimeError("连接失败")) == []
    assert await f.step(RuntimeError("连接失败")) == []
    got = await f.step(RuntimeError("连接失败"))
    assert len(got) == 1 and got[0]["data"]["cause"] == "degraded"
    assert await f.step(RuntimeError("连接失败")) == []  # 不重复
    (inc,) = f.store.list()
    assert inc["category"] == "monitor" and inc["degraded"]
    await f.step(result())
    assert f.store.list() == []  # 监听恢复：降级事件关闭
    assert not f.store.get(inc["incident_id"])["degraded"]


@arun
async def test_restart_does_not_repeat_alert(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    await f.step(result())
    assert len(await f.step(result("blue", "latent", reasons=["x"], escalated=True))) == 1
    f.s = f.new_sentinel()  # 重启：状态从磁盘恢复
    assert await f.step(result("blue", "latent")) == []
    assert len(f.store.list()[0]["pending_alerts"]) == 1
    # 间隔没到不检测
    f.s = f.new_sentinel()
    assert await f.step(minutes=3) == []


@arun
async def test_clock_going_back_resets_baseline(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    await f.step(result(), minutes=600)
    assert len(await f.step(result("red", "accelerating", reasons=["x"], escalated=True), minutes=60)) == 1
    # 模拟矿井重置：时钟回到更早的时刻，旧基线（红色）不能继续压住新历史里的黄色
    f.now_min = 100
    got = await f.step(result("yellow", "accelerating", reasons=["x"], escalated=True), minutes=0)
    assert [a["data"]["cause"] for a in got] == ["anomaly"]


@arun
async def test_followup_due(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    await f.step(result("blue", "latent", reasons=["x"], escalated=True))
    (inc,) = f.store.list()
    f.store.append(inc["incident_id"], f.mine_now(), "followup_added", "agent", {"followup_id": "FU-1", "due_at": "2026-01-01T01:00:00", "expect": "注惰 2 小时后复评"})
    assert await f.step(result("blue", "latent"), minutes=10) == []  # 00:20，未到
    got = await f.step(result("blue", "latent"), minutes=45)  # 01:05，到点
    assert [a["data"]["cause"] for a in got] == ["followup_due"]
    assert await f.step(result("blue", "latent")) == []  # 只触发一次
    v = f.store.get(inc["incident_id"])
    assert v["followups"][0]["state"] == "due"


@arun
async def test_mine_alarm_ingested_once(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    f.alarms = [{"alarm_id": "AL-1", "sensor_id": "SM-1201-UC", "message": "上隅角 CO 超过报警值", "acknowledged": False, "value": 60, "threshold": 24}]
    got = await f.step(result())
    assert [a["data"]["cause"] for a in got] == ["mine_alarm"]
    assert await f.step(result()) == []
    # 同一轮风险检测已为该工作面告警的，矿井报警只记已见
    f.alarms.append({"alarm_id": "AL-2", "sensor_id": "SM-1201-UC", "message": "m", "acknowledged": False})
    got = await f.step(result("blue", "latent", alarms=["R@S"], reasons=["新的实时报警"], escalated=True))
    assert [a["data"]["cause"] for a in got] == ["anomaly"]


@arun
async def test_approval_timeout_escalates_once(tmp_path, monkeypatch):
    from datetime import datetime

    f = Fake(tmp_path, monkeypatch)
    await f.step(result("blue", "latent", reasons=["x"], escalated=True))
    (inc,) = f.store.list()
    iid = inc["incident_id"]
    base = datetime.fromisoformat(f.store.entries(iid)[-1]["wall"]).timestamp()
    f.store.append(iid, f.mine_now(), "note", "approval", {"type": "approval_pending", "approval_key": "c1", "tool": "publish_warning", "confirmer": "通风部门负责人", "timeout_min": 10, "escalate_to": "值班长"})
    f.store.append(iid, f.mine_now(), "note", "approval", {"type": "approval_pending", "approval_key": "c2", "tool": "escalate_emergency", "confirmer": "矿长", "timeout_min": 15, "escalate_to": "矿长"})
    asked = datetime.fromisoformat(f.store.entries(iid)[-1]["wall"]).timestamp()
    f.s.wall = lambda: asked + 5 * 60  # type: ignore[method-assign]
    assert [a["data"]["cause"] for a in await f.step(result("blue", "latent"))] == []  # 才等 5 分钟
    f.s.wall = lambda: asked + 11 * 60  # type: ignore[method-assign]
    got = await f.step(result("blue", "latent"))
    assert [a["data"]["cause"] for a in got] == ["approval_timeout"] and "publish_warning" in got[0]["data"]["summary"] and "值班长" in got[0]["data"]["summary"]
    f.s.wall = lambda: asked + 12 * 60  # type: ignore[method-assign]
    assert await f.step(result("blue", "latent")) == []  # 只升级一次
    # 其中一个有了结果就不再算未决；另一个超过自己的时限（15 分钟）才升级
    f.store.append(iid, f.mine_now(), "note", "approval", {"type": "approval_decided", "approval_key": "c1", "outcome": "rejected"})
    f.s.wall = lambda: asked + 16 * 60  # type: ignore[method-assign]
    got = await f.step(result("blue", "latent"))
    assert [a["data"]["tool"] for a in got] == ["escalate_emergency"]
    v = f.store.get(iid)
    assert [(a["approval_key"], a["escalated"]) for a in v["pending_approvals"]] == [("c2", True)]
    assert base > 0


@arun
async def test_auto_timeout_approvals_are_left_to_the_plugin(tmp_path, monkeypatch):
    """on_timeout 为 approve / reject 的确认由审批插件到点处理，监听器不重复升级；超过两倍批准超时仍未决才兜底（插件已经挂了）。"""
    from datetime import datetime

    f = Fake(tmp_path, monkeypatch)
    await f.step(result("blue", "latent", reasons=["x"], escalated=True))
    (inc,) = f.store.list()
    iid = inc["incident_id"]
    f.store.append(iid, f.mine_now(), "note", "approval", {"type": "approval_pending", "approval_key": "a1", "tool": "start_monitoring", "confirmer": "值班长", "timeout_min": 5, "escalate_to": "总工程师", "on_timeout": "approve"})
    asked = datetime.fromisoformat(f.store.entries(iid)[-1]["wall"]).timestamp()
    f.s.wall = lambda: asked + 6 * 60  # type: ignore[method-assign]
    assert await f.step(result("blue", "latent")) == []  # 超时了，但插件会自动处理
    f.s.wall = lambda: asked + 11 * 60  # type: ignore[method-assign]
    got = await f.step(result("blue", "latent"))  # 两倍超时仍未决：兜底升级
    assert [a["data"]["tool"] for a in got] == ["start_monitoring"]


@arun
async def test_approval_timeout_escalates_even_when_mine_clock_is_down(tmp_path, monkeypatch):
    """矿井接口故障（取不到时钟）时，人工确认的批准超时仍要升级：超时按真实时间算，与矿井无关。"""
    from datetime import datetime

    f = Fake(tmp_path, monkeypatch)
    await f.step(result("blue", "latent", reasons=["x"], escalated=True))
    (inc,) = f.store.list()
    iid = inc["incident_id"]
    f.store.append(iid, f.mine_now(), "note", "approval", {"type": "approval_pending", "approval_key": "c1", "tool": "publish_warning", "confirmer": "通风部门负责人", "timeout_min": 10, "escalate_to": "值班长"})
    asked = datetime.fromisoformat(f.store.entries(iid)[-1]["wall"]).timestamp()
    real_call = f.call

    async def down(tool, args):
        if tool == "get_clock":
            raise ConnectionError("矿井接口不通")
        return await real_call(tool, args)

    f.s.call = down  # type: ignore[method-assign]
    f.s.wall = lambda: asked + 11 * 60  # type: ignore[method-assign]
    with pytest.raises(ConnectionError):
        await f.s.tick()
    v = f.store.get(iid)
    assert [(a["approval_key"], a["escalated"]) for a in v["pending_approvals"]] == [("c1", True)]
    assert [x["data"]["cause"] for x in f.store.entries(iid) if x["kind"] == "alert"][-1] == "approval_timeout"
    with pytest.raises(ConnectionError):
        await f.s.tick()  # 再来一次不重复升级
    assert [x["data"]["cause"] for x in f.store.entries(iid) if x["kind"] == "alert"].count("approval_timeout") == 1


def test_watch_panels_can_be_overridden_by_env(tmp_path, monkeypatch):
    # 缺省取技能参数；RESCUE_WATCH_PANELS 覆盖（换了演示矿井的工作面名称时用）
    assert Fake(tmp_path / "a", monkeypatch).s.panels == ["1201"]
    monkeypatch.setenv("RESCUE_WATCH_PANELS", " 211016 , 1202 ")
    assert Fake(tmp_path / "b", monkeypatch).s.panels == ["211016", "1202"]

