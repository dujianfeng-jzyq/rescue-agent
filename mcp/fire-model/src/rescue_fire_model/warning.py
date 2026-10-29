"""采空区自燃预警分级。

输入：上隅角、回风巷、采空区束管的最近 24 小时读数（经数据质量检测）。
输出：预警等级、触发条件、指标值、数据质量说明。所有数值都来自计算，供 Agent 引用，不由 Agent 估算。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from rescue_contract.client import MineData
from rescue_contract.models import Measurand, Reading, SensorSource, WarningLevel

from .quality import check_series, cross_check

# 每座矿井审定后的阈值通过 RESCUE_FIRE_THRESHOLDS 指定；默认是演示占位值
THRESHOLDS_PATH = Path(os.environ.get("RESCUE_FIRE_THRESHOLDS") or Path(__file__).with_name("thresholds.yaml"))
ORDER = [WarningLevel.NONE, WarningLevel.BLUE, WarningLevel.YELLOW, WarningLevel.ORANGE, WarningLevel.RED]
NAMES = {"blue": "蓝色（关注）", "yellow": "黄色（预警）", "orange": "橙色（严重）", "red": "红色（危险）"}
LABELS = {
    "uc_co_ppm": "上隅角CO(ppm)",
    "tube_co_ppm": "束管CO最大值(ppm)",
    "tube_co_rate_ppm_h": "束管CO上升速率(ppm/h)",
    "c2h4_ppm": "乙烯(ppm)",
    "c2h2_ppm": "乙炔(ppm)",
    "graham_ratio": "格雷哈姆系数",
}


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict[str, dict[str, float]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def slope_per_hour(rs: list[Reading]) -> float | None:
    pts = [(r.ts.timestamp() / 3600, r.value) for r in rs if r.value is not None]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    den = sum((x - mx) ** 2 for x, _ in pts)
    return None if den == 0 else sum((x - mx) * (y - my) for x, y in pts) / den


def graham_ratio(co_ppm: float, o2_pct: float, n2_pct: float) -> float | None:
    deficit = 0.265 * n2_pct - o2_pct
    if deficit < 0.3:
        return None
    return 100 * (co_ppm / 10000) / deficit


@dataclass
class Assessment:
    as_of: datetime
    level: WarningLevel
    indicators: dict[str, float | None]
    triggered: list[str]
    data_quality: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "level": self.level.value,
            "level_name": NAMES.get(self.level.value, "无"),
            "indicators": {LABELS.get(k, k): (round(v, 3) if v is not None else None) for k, v in self.indicators.items()},
            "triggered": self.triggered,
            "data_quality": self.data_quality,
            "notes": self.notes,
            "method": "阈值分级（thresholds.yaml）。阈值为演示占位值，需按本矿自然发火预测预报制度设定",
        }


def classify(ind: dict[str, float | None], thresholds: dict[str, dict[str, float]]) -> tuple[WarningLevel, list[str]]:
    level, reasons = WarningLevel.NONE, []
    for name in ("blue", "yellow", "orange", "red"):
        hits = [f"{LABELS[k]} {ind[k]:.2f} ≥ {v}" for k, v in thresholds.get(name, {}).items() if ind.get(k) is not None and ind[k] >= v]
        if hits:
            level = WarningLevel(name)
            reasons = [f"{NAMES[name]}：" + "；".join(hits)]
    return level, reasons


def _series(readings: list[Reading], sensor_id: str, m: Measurand) -> list[Reading]:
    return sorted((r for r in readings if r.sensor_id == sensor_id and r.measurand == m), key=lambda r: r.ts)


async def assess(md: MineData, panel: str = "1201", thresholds: dict | None = None) -> Assessment:
    thresholds = thresholds or load_thresholds()
    now = await md.now()
    sensors = [s for s in await md.sensors() if panel in s.id]
    readings = await md.readings([s.id for s in sensors], start=now - timedelta(hours=24), end=now)
    dq: list[str] = []
    notes: list[str] = []

    def usable(sid: str, m: Measurand, sibling: Measurand | None = None) -> list[Reading]:
        sib = _series(readings, sid, sibling) if sibling else None
        rep = check_series(f"{sid}/{m.value}", _series(readings, sid, m), sib)
        dq.extend(rep.flags)
        return rep.usable

    # 上隅角 CO，用回风巷 CO 交叉校核
    uc = usable(f"SM-{panel}-UC", Measurand.CO)
    ret = usable(f"SM-{panel}-RET", Measurand.CO)
    uc_co = uc[-1].value if uc else None
    if uc and ret and not cross_check(uc, ret, ratio=0.85):
        dq.append(f"SM-{panel}-UC/CO：与回风巷 CO 不匹配（{uc[-1].value:.1f} vs {ret[-1].value:.1f}），疑似漂移，已排除")
        uc_co = None

    tube_co = tube_rate = c2h4 = c2h2 = graham = None
    tubes = [s for s in sensors if s.source == SensorSource.BUNDLE_TUBE]
    for t in tubes:
        co = usable(t.id, Measurand.CO)
        if not co:
            continue
        last = co[-1].value
        if tube_co is None or last > tube_co:
            tube_co = last
            recent = [r for r in co if r.ts >= now - timedelta(hours=12)]
            tube_rate = slope_per_hour(recent)
            o2, n2 = usable(t.id, Measurand.O2), usable(t.id, Measurand.N2)
            if o2 and n2:
                graham = graham_ratio(last, o2[-1].value, n2[-1].value)
                if n2[-1].value > 85:
                    notes.append(f"{t.id} 氮气浓度 {n2[-1].value:.1f}%，可能处于注氮影响范围，格雷哈姆系数偏低失真")
        for m, cur in ((Measurand.C2H4, "c2h4"), (Measurand.C2H2, "c2h2")):
            s = usable(t.id, m, sibling=Measurand.CO)  # 未出现时恒为 0，用同测点 CO 判断测点是否在工作
            if s:
                v = s[-1].value
                if cur == "c2h4":
                    c2h4 = v if c2h4 is None else max(c2h4, v)
                else:
                    c2h2 = v if c2h2 is None else max(c2h2, v)
    if not tubes or tube_co is None:
        notes.append("没有可用的束管 CO 数据，预警等级可能偏低，需人工复核")

    ind = {
        "uc_co_ppm": uc_co,
        "tube_co_ppm": tube_co,
        "tube_co_rate_ppm_h": tube_rate,
        "c2h4_ppm": c2h4,
        "c2h2_ppm": c2h2,
        "graham_ratio": graham,
    }
    level, triggered = classify(ind, thresholds)
    return Assessment(as_of=now, level=level, indicators=ind, triggered=triggered, data_quality=dq, notes=notes)


def level_index(level: WarningLevel) -> int:
    return ORDER.index(level)
