"""模拟引擎：时钟、采空区自燃过程模型、传感器仿真和处置响应。

过程模型是演示用的简化集中参数模型，只追求“趋势合理、能响应处置”，不用于真实研判：
- 高温点温度 T：dT/dt = k·(O₂/20.9)·exp(β(T−30)) − h·(T − T_amb)
- 高温点氧浓度由漏风、采空区深度（随推进增加）和注氮稀释决定
- 标志气体浓度是温度和氧浓度的经验函数（CO 随温度指数增长，C₂H₄、C₂H₂ 分别在阈值温度以上出现）

TODO(专家): 用程序升温实验数据和本矿标志气体临界值校准气体生成函数和全部参数。
"""

from __future__ import annotations

import math
import random
import time
import zlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from rescue_contract.models import (
    EventKind,
    FireArea,
    FireAreaStatus,
    Measurand,
    MineEvent,
    PersonLocation,
    Quality,
    Reading,
    SensorSource,
)

from .mine import SENSOR_BY_ID, SENSORS

STEP_H = 0.25  # 积分步长 15 分钟
SAMPLE_INTERVAL_H = {
    SensorSource.SAFETY_MONITOR: 0.25,
    SensorSource.BUNDLE_TUBE: 1.0,
    SensorSource.TEMPERATURE: 0.5,
}
MAX_POINTS = 5000

# 报警阈值（演示用）。TODO(专家): 按《煤矿安全规程》和本矿安全监控设置核对。
ALARM_THRESHOLDS = {
    ("SM-1201-UC", Measurand.CO): 24.0,  # ppm
    ("SM-1201-UC", Measurand.CH4): 1.0,  # %
    ("SM-1201-RET", Measurand.CO): 24.0,
    ("SM-1201-RET", Measurand.CH4): 1.0,
}


@dataclass(frozen=True)
class ProcessParams:
    t0_c: float = 32.0  # 初始高温点温度
    ambient_c: float = 25.0
    k_ox: float = 0.5  # T=30℃、空气氧浓度下的升温速率，℃/h
    beta: float = 0.03  # 温度敏感系数，1/℃
    h_loss: float = 0.02  # 散热系数，1/h
    leak_m3_min: float = 100.0  # 采空区漏风量
    hotspot_depth_m: float = 35.0  # 初始高温点距工作面距离
    advance_m_day: float = 4.0  # 工作面推进速度
    o2_decay_len_m: float = 45.0  # 漏风氧随采空区深度衰减的特征长度
    ch4_goaf_pct: float = 4.0
    ch4_uc_pct: float = 0.45
    open_fire_c: float = 350.0  # 超过即视为出现明火（演示判据）
    max_heating_c_h: float = 25.0  # 供氧限制下的最大升温速率
    n2_penetration: float = 0.9  # 注氮有效进入高温区的比例
    noise_rel: float = 0.02


@dataclass(frozen=True)
class Fault:
    sensor_id: str
    kind: str  # drift | stuck | dropout | spike
    start_h: float
    end_h: float | None = None
    measurand: Measurand | None = None
    magnitude: float = 0.0


@dataclass(frozen=True)
class Intervention:
    """一次处置（或剧本事件）对过程的作用。type 与契约 StepType 的取值一致，另有剧本专用的 leak_change。"""

    type: str
    at_h: float
    params: dict = field(default_factory=dict)
    roadways: tuple[str, ...] = ()
    source: str = "control"  # control | script | action


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    description: str
    start: datetime
    duration_h: float
    speed: float  # 模拟秒 / 真实秒
    params: ProcessParams
    faults: tuple[Fault, ...] = ()
    script: tuple[Intervention, ...] = ()
    seed: int = 7


@dataclass
class _State:
    t_h: float
    temp_c: float
    n2_m3_h: float = 0.0
    k_mult: float = 1.0
    leak_mult: float = 1.0
    h_mult: float = 1.0
    sealed: bool = False
    sealed_at_h: float | None = None
    unsealed_at_h: float | None = None
    advancing: bool = True
    depth_m: float = 0.0


class Clock:
    """模拟时钟。manual=True 时只随 advance() 前进（测试和评测用）。"""

    def __init__(self, speed: float, manual: bool = False) -> None:
        self.speed = speed
        self.manual = manual
        self.paused = manual
        self._offset_h = 0.0
        self._wall_ref = time.monotonic()

    def hours(self) -> float:
        if self.paused:
            return self._offset_h
        return self._offset_h + (time.monotonic() - self._wall_ref) * self.speed / 3600

    def advance(self, hours: float) -> None:
        self._offset_h = self.hours() + hours
        self._wall_ref = time.monotonic()

    def set_paused(self, paused: bool) -> None:
        self._offset_h = self.hours()
        self._wall_ref = time.monotonic()
        self.paused = paused or self.manual

    def set_speed(self, speed: float) -> None:
        self._offset_h = self.hours()
        self._wall_ref = time.monotonic()
        self.speed = speed


class SimEngine:
    def __init__(self, scenario: Scenario, manual_clock: bool = False) -> None:
        self.scenario = scenario
        self.clock = Clock(scenario.speed, manual=manual_clock)
        p = scenario.params
        self._history: list[_State] = [_State(t_h=0.0, temp_c=p.t0_c, depth_m=p.hotspot_depth_m)]
        self._interventions: list[Intervention] = sorted(scenario.script, key=lambda i: i.at_h)
        self._applied = 0
        self.evacuated_roadways: set[str] = set()
        self.action_log: list[Intervention] = []

    # ── 时间 ──────────────────────────────────────────────────────────────

    def now_h(self) -> float:
        return min(self.clock.hours(), self.scenario.duration_h)

    def now(self) -> datetime:
        return self.at(self.now_h())

    def at(self, t_h: float) -> datetime:
        return self.scenario.start + timedelta(hours=t_h)

    def hours_of(self, ts: datetime) -> float:
        return (ts - self.scenario.start).total_seconds() / 3600

    # ── 积分 ──────────────────────────────────────────────────────────────

    def _advance_to(self, t_h: float) -> None:
        p = self.scenario.params
        while self._history[-1].t_h + STEP_H <= t_h + 1e-9:
            s = replace(self._history[-1])
            # 先应用到期的剧本事件和处置
            while self._applied < len(self._interventions) and self._interventions[self._applied].at_h <= s.t_h + 1e-9:
                self._apply(s, self._interventions[self._applied])
                self._applied += 1
            o2 = self._o2_hot(s)
            # 高温阶段受供氧限制：升温速率上限与氧浓度成正比
            rate = min(p.max_heating_c_h, p.k_ox * s.k_mult * math.exp(p.beta * (s.temp_c - 30))) * (o2 / 20.9)
            loss = p.h_loss * s.h_mult * (s.temp_c - p.ambient_c)
            s.temp_c = min(1200.0, max(p.ambient_c, s.temp_c + (rate - loss) * STEP_H))
            if s.advancing and not s.sealed:
                s.depth_m += p.advance_m_day / 24 * STEP_H
            s.t_h = round(s.t_h + STEP_H, 6)
            self._history.append(s)

    def _apply(self, s: _State, iv: Intervention) -> None:
        kind, prm = iv.type, iv.params
        if kind == "inert_injection":
            s.n2_m3_h = max(0.0, s.n2_m3_h + float(prm.get("rate_m3_h", 600)))
        elif kind == "stop_inert_injection":
            s.n2_m3_h = 0.0
        elif kind == "grouting":
            s.k_mult *= 0.5
            s.h_mult *= 1.5
        elif kind == "inhibitor_spray":
            s.k_mult *= 0.75
        elif kind in ("leak_sealing", "pressure_balancing"):
            s.leak_mult *= 1 - min(0.9, float(prm.get("reduction", 0.5)))
        elif kind in ("temporary_seal", "permanent_seal"):
            if not s.sealed:
                s.sealed, s.sealed_at_h, s.unsealed_at_h = True, s.t_h, None
            s.leak_mult *= 0.1 if kind == "temporary_seal" else 0.3
        elif kind == "unseal":
            if s.sealed:
                s.unsealed_at_h = s.t_h
            s.sealed, s.leak_mult = False, max(s.leak_mult, 0.5)
        elif kind == "leak_change":  # 剧本：漏风突然增大（如顶板垮落、密闭失效）
            s.leak_mult *= float(prm.get("factor", 2.0))
        elif kind == "stop_advance":
            s.advancing = False
        elif kind == "evacuate":
            self.evacuated_roadways.update(iv.roadways or ("*",))

    def apply(self, iv_type: str, params: dict | None = None, roadways: list[str] | None = None, source: str = "control") -> Intervention:
        """在当前模拟时刻施加一次处置。"""
        t = self.now_h()
        self._advance_to(t)
        iv = Intervention(type=iv_type, at_h=self._history[-1].t_h, params=params or {}, roadways=tuple(roadways or ()), source=source)
        self._apply(self._history[-1], iv)
        self.action_log.append(iv)
        return iv

    def state_at(self, t_h: float) -> _State:
        self._advance_to(t_h)
        idx = min(int(round(t_h / STEP_H)), len(self._history) - 1)
        return self._history[max(0, idx)]

    # ── 物理量 ────────────────────────────────────────────────────────────

    def _air_fraction(self, s: _State, depth_m: float) -> float:
        p = self.scenario.params
        leak = p.leak_m3_min * 60 * s.leak_mult
        n2 = s.n2_m3_h * p.n2_penetration
        return math.exp(-depth_m / p.o2_decay_len_m) * leak / (leak + n2 + 1e-9)

    def _o2_hot(self, s: _State) -> float:
        # 高温点耗氧：温度越高，局部氧浓度越低
        consumption = 1 + 0.01 * max(0.0, s.temp_c - 60)
        return 20.9 * self._air_fraction(s, s.depth_m) / consumption

    def hotspot(self, t_h: float | None = None) -> dict:
        """高温点真实状态（仅供模拟控制台和评测使用，不暴露给智能体）。"""
        s = self.state_at(self.now_h() if t_h is None else t_h)
        return {
            "t_h": s.t_h,
            "temperature_c": round(s.temp_c, 1),
            "o2_pct": round(self._o2_hot(s), 2),
            "depth_m": round(s.depth_m, 1),
            "sealed": s.sealed,
            "open_fire": s.temp_c >= self.scenario.params.open_fire_c,
            "n2_m3_h": s.n2_m3_h,
        }

    def fire_areas(self) -> list[FireArea]:
        """火区台账：封闭过的火区。封闭和启封是人工处置留下的记录，不是隐藏的真值。"""
        s = self.state_at(self.now_h())
        if s.sealed_at_h is None:
            return []
        return [
            FireArea(
                area_id="FA-1201-GOAF",
                panel="1201",
                name="1201 工作面采空区火区",
                status=FireAreaStatus.SEALED if s.sealed else FireAreaStatus.UNSEALED,
                sealed_at=self.at(s.sealed_at_h),
                unsealed_at=None if s.unsealed_at_h is None else self.at(s.unsealed_at_h),
            )
        ]

    def _hot_gases(self, s: _State) -> dict[Measurand, float]:
        p = self.scenario.params
        T, o2 = s.temp_c, self._o2_hot(s)
        ox = o2 / 20.9 + 0.05
        # 经验函数，高温段饱和。TODO(专家): 用程序升温实验数据校准
        co = 20 + min(15 * math.exp(0.04 * (T - 30)), 30000) * ox
        c2h4 = 0.0 if T < 90 else min(0.8 * (math.exp(0.035 * (T - 90)) - 1), 800) * ox
        c2h2 = 0.0 if T < 220 else min(0.3 * (math.exp(0.02 * (T - 220)) - 1), 300) * ox
        co2 = 0.2 + min(0.004 * max(0.0, T - 30), 8.0) * ox
        n2_frac = (s.n2_m3_h * p.n2_penetration) / (p.leak_m3_min * 60 * s.leak_mult + s.n2_m3_h * p.n2_penetration + 1e-9)
        ch4 = p.ch4_goaf_pct * (1 - 0.3 * n2_frac)
        return {Measurand.O2: o2, Measurand.CO: co, Measurand.C2H4: c2h4, Measurand.C2H2: c2h2, Measurand.CO2: co2, Measurand.CH4: ch4}

    def _true_value(self, sensor_id: str, m: Measurand, s: _State) -> float | None:
        p = self.scenario.params
        sensor = SENSOR_BY_ID[sensor_id]
        hot = self._hot_gases(s)
        if sensor.source in (SensorSource.BUNDLE_TUBE, SensorSource.TEMPERATURE):
            d_i = (sensor.goaf_depth_m or 0) + (s.depth_m - p.hotspot_depth_m)
            near = math.exp(-abs((sensor.goaf_depth_m or 0) - p.hotspot_depth_m) / 25)
            if sensor.source == SensorSource.TEMPERATURE:
                return p.ambient_c + (s.temp_c - p.ambient_c) * math.exp(-abs((sensor.goaf_depth_m or 0) - p.hotspot_depth_m) / 15)
            o2_bg = 20.9 * self._air_fraction(s, d_i)
            bg = {
                Measurand.O2: o2_bg,
                Measurand.CO: 8.0,
                Measurand.C2H4: 0.0,
                Measurand.C2H2: 0.0,
                Measurand.CO2: 0.15,
                Measurand.CH4: p.ch4_goaf_pct * 0.8,
            }
            if m == Measurand.N2:
                o2 = self._true_value(sensor_id, Measurand.O2, s) or 0
                co2 = self._true_value(sensor_id, Measurand.CO2, s) or 0
                ch4 = self._true_value(sensor_id, Measurand.CH4, s) or 0
                return max(0.0, 100 - o2 - co2 - ch4 - 0.93)
            return bg[m] + near * (hot[m] - bg[m])
        # 安全监控测点：封闭后断电，读不到数
        if s.sealed and sensor_id in ("SM-1201-UC", "SM-1201-RET", "SM-1201-IN"):
            return None
        leak_share = min(1.0, s.leak_mult)
        uc = {
            Measurand.CO: 2 + 0.12 * hot[Measurand.CO] * leak_share,
            Measurand.CH4: p.ch4_uc_pct + 0.05 * hot[Measurand.CH4] * leak_share,
            Measurand.O2: 20.6 - 0.2 * leak_share,
            Measurand.TEMPERATURE: 24 + 0.02 * (s.temp_c - 30),
            Measurand.WIND_SPEED: 2.1,
        }
        if sensor_id == "SM-1201-UC":
            return uc.get(m)
        if sensor_id == "SM-1201-RET":
            return {
                Measurand.CO: uc[Measurand.CO] * 0.85,
                Measurand.CH4: uc[Measurand.CH4] * 0.9,
                Measurand.TEMPERATURE: uc[Measurand.TEMPERATURE] - 0.5,
                Measurand.WIND_SPEED: 2.0,
            }.get(m)
        if sensor_id == "SM-RET-MAIN":
            return {
                Measurand.CO: 1 + uc[Measurand.CO] * 0.25,
                Measurand.CH4: 0.2 + uc[Measurand.CH4] * 0.25,
                Measurand.WIND_SPEED: 3.5,
            }.get(m)
        if sensor_id == "SM-1201-IN":
            return {Measurand.CO: 0.5, Measurand.CH4: 0.05, Measurand.WIND_SPEED: 2.3}.get(m)
        return None

    # ── 传感器读数 ────────────────────────────────────────────────────────

    def _noise(self, sensor_id: str, m: Measurand, t_h: float) -> float:
        key = f"{self.scenario.seed}:{sensor_id}:{m.value}:{int(round(t_h * 4))}"
        return random.Random(zlib.crc32(key.encode())).gauss(0, 1)

    def _noisy(self, sensor_id: str, m: Measurand, t_h: float) -> float | None:
        value = self._true_value(sensor_id, m, self.state_at(t_h))
        if value is None:
            return None
        return value * (1 + self.scenario.params.noise_rel * self._noise(sensor_id, m, t_h))

    def reading(self, sensor_id: str, m: Measurand, t_h: float) -> Reading:
        value = self._noisy(sensor_id, m, t_h)
        quality = Quality.OK
        for f in self.scenario.faults:
            if f.sensor_id != sensor_id or (f.measurand and f.measurand != m):
                continue
            if t_h < f.start_h or (f.end_h is not None and t_h > f.end_h):
                continue
            if f.kind == "dropout":
                value = None
            elif f.kind == "drift" and value is not None:
                value += f.magnitude * (t_h - f.start_h)
            elif f.kind == "stuck":
                value = self._noisy(sensor_id, m, f.start_h)
            elif f.kind == "spike" and value is not None and t_h - f.start_h < 1:
                value *= f.magnitude
        # 注意：漂移、卡值不标记 SUSPECT。真实监控系统也不会自报故障，识别它们是 fire-model 数据质量检测的职责（标准场景 6 专门检验这一点）。
        if value is None:
            quality = Quality.MISSING
        else:
            if m in (Measurand.CO, Measurand.C2H4, Measurand.C2H2, Measurand.CH4, Measurand.CO2):
                value = max(0.0, value)
            value = round(value, 3)
        return Reading(sensor_id=sensor_id, measurand=m, ts=self.at(t_h), value=value, quality=quality)

    def readings(self, sensor_ids: list[str], start_h: float, end_h: float, measurands: list[Measurand] | None = None) -> list[Reading]:
        end_h = min(end_h, self.now_h())
        out: list[Reading] = []
        for sid in sensor_ids:
            sensor = SENSOR_BY_ID[sid]
            step = SAMPLE_INTERVAL_H[sensor.source]
            t = math.ceil(max(0.0, start_h) / step) * step
            while t <= end_h + 1e-9 and len(out) < MAX_POINTS:
                for m in sensor.measurands:
                    if measurands is None or m in measurands:
                        out.append(self.reading(sid, m, t))
                t = round(t + step, 6)
        return out

    def latest(self, sensor_ids: list[str]) -> list[Reading]:
        out = []
        for sid in sensor_ids:
            sensor = SENSOR_BY_ID[sid]
            step = SAMPLE_INTERVAL_H[sensor.source]
            t = math.floor(self.now_h() / step) * step
            out.extend(self.reading(sid, m, t) for m in sensor.measurands)
        return out

    def events(self, start_h: float, end_h: float) -> list[MineEvent]:
        end_h = min(end_h, self.now_h())
        out: list[MineEvent] = []
        active: set[tuple[str, Measurand]] = set()
        for (sid, m), limit in ALARM_THRESHOLDS.items():
            for r in self.readings([sid], start_h, end_h, [m]):
                key = (sid, m)
                if r.quality == Quality.MISSING:
                    if key not in active:
                        out.append(
                            MineEvent(
                                event_id=f"F-{sid}-{m.value}-{r.ts:%Y%m%d%H%M}",
                                kind=EventKind.SENSOR_FAULT,
                                ts=r.ts,
                                sensor_id=sid,
                                message=f"{SENSOR_BY_ID[sid].name} {m.value} 无数据",
                            )
                        )
                        active.add(key)
                    continue
                if r.value is not None and r.value > limit and key not in active:
                    out.append(
                        MineEvent(
                            event_id=f"A-{sid}-{m.value}-{r.ts:%Y%m%d%H%M}",
                            kind=EventKind.SENSOR_ALARM,
                            ts=r.ts,
                            sensor_id=sid,
                            roadway_id=SENSOR_BY_ID[sid].roadway_id,
                            message=f"{SENSOR_BY_ID[sid].name} {m.value}={r.value} 超过报警值 {limit}",
                        )
                    )
                    active.add(key)
                elif r.value is not None and r.value <= limit:
                    active.discard(key)
        return sorted(out, key=lambda e: e.ts)

    # ── 人员 ──────────────────────────────────────────────────────────────

    def personnel(self) -> list[PersonLocation]:
        now = self.now()
        shift = "ABC"[now.hour // 8]
        layout = [
            ("R-1201-FACE", "采煤工", 9),
            ("R-1201-IN", "运输工", 3),
            ("R-1201-RET", "瓦斯检查员", 1),
            ("R-1201-RET", "通风工", 1),
            ("R-1202-IN", "掘进工", 6),
            ("R-MAIN-IN", "电钳工", 2),
        ]
        out = []
        n = 0
        for roadway, role, count in layout:
            for _ in range(count):
                n += 1
                if "*" in self.evacuated_roadways or roadway in self.evacuated_roadways:
                    continue
                out.append(PersonLocation(person_id=f"{shift}{n:03d}", role=role, team=f"{shift}班", roadway_id=roadway, ts=now))
        return out


def all_sensor_ids() -> list[str]:
    return [s.id for s in SENSORS]
