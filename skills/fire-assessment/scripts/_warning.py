"""采空区自燃预警分级与自燃发展阶段（由 mcp/fire-model 的 warning.py、stage.py 迁移，判据不变）。

输入：证据里上隅角、回风巷、采空区束管的读数（经数据质量检测）。
输出：预警等级、触发条件、指标值、数据质量说明；阶段与等级独立判定。所有数值都来自计算，智能体照抄。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from _evidence import as_of, sensors, series
from _quality import check_series, cross_check, slope_per_hour, ts

LEVELS = ["none", "blue", "yellow", "orange", "red"]
NAMES = {"none": "无", "blue": "蓝色（关注）", "yellow": "黄色（预警）", "orange": "橙色（严重）", "red": "红色（危险）"}
LABELS = {
    "uc_co_ppm": "上隅角CO(ppm)",
    "tube_co_ppm": "束管CO最大值(ppm)",
    "tube_co_rate_ppm_h": "束管CO上升速率(ppm/h)",
    "c2h4_ppm": "乙烯(ppm)",
    "c2h2_ppm": "乙炔(ppm)",
    "graham_ratio": "格雷哈姆系数",
}
STAGES = ["unknown", "no_sign", "slow_oxidation", "accelerating", "intense"]
STAGE_NAMES = {
    "unknown": "无法判定（束管 CO 数据不可用）",
    "no_sign": "未见自燃征兆",
    "slow_oxidation": "缓慢氧化",
    "accelerating": "加速氧化",
    "intense": "激烈氧化，接近明火",
}
# 火区封闭后，密闭内的安全监控测点（上隅角、回风巷）断电、读不到数，封闭前的读数也不再代表现状：不参与分级，按束管监测。
# TODO(专家): 哪些测点在密闭内按本矿封闭范围定；目前按“工作面的上隅角、回风巷测点”处理。
SEALED_NOTE = "火区已封闭：上隅角、回风巷安全监控测点在密闭内断电，不参与分级，按束管监测"


def panel_sealed(ev: dict[str, Any]) -> bool:
    """火区台账里本工作面有状态为已封闭的火区。没有台账按未封闭（照常要求上隅角、回风巷测点）。"""
    return any(a.get("panel") == ev["panel"] and a.get("status") == "sealed" for a in ev.get("fire_areas") or [])


METHOD = "阈值分级（references/thresholds.yaml）。阈值为演示占位值，需按本矿自然发火预测预报制度设定；自燃发展阶段与等级独立判定"


def graham_ratio(co_ppm: float, o2_pct: float, n2_pct: float) -> float | None:
    deficit = 0.265 * n2_pct - o2_pct
    if deficit < 0.3:
        return None
    return 100 * (co_ppm / 10000) / deficit


def classify(ind: dict[str, float | None], thresholds: dict[str, Any]) -> tuple[str, list[str]]:
    level, reasons = "none", []
    for name in ("blue", "yellow", "orange", "red"):
        hits = [f"{LABELS[k]} {ind[k]:.2f} ≥ {v}" for k, v in (thresholds.get(name) or {}).items() if ind.get(k) is not None and ind[k] >= v]
        if hits:
            level = name
            reasons = [f"{NAMES[name]}：" + "；".join(hits)]
    return level, reasons


def classify_stage(ind: dict[str, float | None], cfg: dict[str, Any]) -> tuple[str, list[str]]:
    """按指标判定阶段，返回（阶段，依据）。自高到低，满足该阶段任一条件即是。"""
    for name in ("intense", "accelerating", "slow_oxidation"):
        hits = [f"{LABELS[k]} {ind[k]:.3f} ≥ {v}" for k, v in (cfg.get(name) or {}).items() if ind.get(k) is not None and ind[k] >= v]
        if hits:
            return name, hits
    if ind.get("tube_co_ppm") is None:
        return "unknown", ["没有可用的束管 CO 数据"]
    return "no_sign", ["各阶段条件均未满足"]


def assess(ev: dict[str, Any], thresholds: dict[str, Any], window_h: float, rate_window_h: float) -> dict[str, Any]:
    panel = ev["panel"]
    now = as_of(ev)
    panel_sensors = [s for s in sensors(ev) if panel in s["id"]]
    dq: list[str] = []
    notes: list[str] = []

    def lowest(key: str) -> float | None:
        """该指标最低一级的预警阈值：读数卡住但达到它时按保守处理保留（check_series 的 keep_at_or_above）"""
        vals = [lv[key] for lv in thresholds.values() if isinstance(lv, dict) and isinstance(lv.get(key), int | float)]
        return min(vals) if vals else None

    def usable(sid: str, m: str, sibling: str | None = None, key: str | None = None) -> list[dict[str, Any]]:
        sib = series(ev, sid, sibling, window_h) if sibling else None
        rep = check_series(f"{sid}/{m}", series(ev, sid, m, window_h), sib, keep_at_or_above=lowest(key) if key else None)
        dq.extend(rep.flags)
        return rep.usable

    # 上隅角 CO，用回风巷 CO 交叉校核；火区已封闭时两者都在密闭内，不用
    if panel_sealed(ev):
        uc, ret = [], []
        notes.append(SEALED_NOTE)
    else:
        # 测点按模拟矿井的编号规则找（待办：对接真实矿井前改为按证据里测点组的角色找）；找不到时写明，不让缺失悄悄发生
        for sid, what in ((f"SM-{panel}-UC", "上隅角 CO 不参与分级"), (f"SM-{panel}-RET", "上隅角 CO 无法与回风巷交叉校核")):
            if not series(ev, sid, "CO", window_h):
                dq.append(f"证据里没有 {sid}/CO 的读数，{what}")
        uc = usable(f"SM-{panel}-UC", "CO", key="uc_co_ppm")
        ret = usable(f"SM-{panel}-RET", "CO")
    uc_co = uc[-1]["value"] if uc else None
    src: dict[str, dict[str, str]] = {}
    if uc_co is not None:
        src["uc_co_ppm"] = {"sensor_id": f"SM-{panel}-UC", "measurand": "CO"}
    if uc and ret and not cross_check(uc, ret, ratio=0.85):
        dq.append(f"SM-{panel}-UC/CO：与回风巷 CO 不匹配（{uc[-1]['value']:.1f} vs {ret[-1]['value']:.1f}），疑似漂移，已排除")
        uc_co = None
        src.pop("uc_co_ppm", None)

    tube_co = tube_rate = c2h4 = c2h2 = graham = None
    tubes = [s for s in panel_sensors if s["source"] == "bundle_tube"]
    for t in tubes:
        co = usable(t["id"], "CO", key="tube_co_ppm")
        if not co:
            continue
        last = co[-1]["value"]
        if tube_co is None or last > tube_co:
            tube_co = last
            src["tube_co_ppm"] = {"sensor_id": t["id"], "measurand": "CO"}
            recent = [r for r in co if ts(r) >= now - timedelta(hours=rate_window_h)]
            tube_rate = slope_per_hour(recent)
            if tube_rate is not None:
                src["tube_co_rate_ppm_h"] = {"sensor_id": t["id"], "measurand": "CO"}
            else:
                src.pop("tube_co_rate_ppm_h", None)
            o2, n2 = usable(t["id"], "O2"), usable(t["id"], "N2")
            if o2 and n2:
                graham = graham_ratio(last, o2[-1]["value"], n2[-1]["value"])
                src.pop("graham_ratio", None)  # 新的最大值测点算得出系数才替换
                if graham is not None:
                    src["graham_ratio"] = {"sensor_id": t["id"], "measurand": "CO"}
                if n2[-1]["value"] > 85:
                    notes.append(f"{t['id']} 氮气浓度 {n2[-1]['value']:.1f}%，可能处于注氮影响范围，格雷哈姆系数偏低失真")
        for m, cur in (("C2H4", "c2h4"), ("C2H2", "c2h2")):
            s = usable(t["id"], m, sibling="CO", key=f"{cur}_ppm")  # 未出现时恒为 0，用同测点 CO 判断测点是否在工作
            if s:
                v = s[-1]["value"]
                prev = c2h4 if cur == "c2h4" else c2h2
                if prev is None or v > prev:
                    src[f"{cur}_ppm"] = {"sensor_id": t["id"], "measurand": m}
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
    src = {k: v for k, v in src.items() if ind.get(k) is not None}
    return {
        "panel": panel,
        "as_of": ev["as_of"],
        "level": level,
        "level_name": NAMES[level],
        "triggered": triggered,
        # 报告和智能体用：保留 3 位小数（与第一版一致）；values 是原值，供阶段判定和趋势选测点
        "indicators": {LABELS[k]: (round(v, 3) if v is not None else None) for k, v in ind.items()},
        "indicator_list": [
            {"key": k, "label": LABELS[k], "value": (round(v, 3) if v is not None else None), "sensor_id": src.get(k, {}).get("sensor_id")}
            for k, v in ind.items()
        ],
        "values": ind,
        "sources": src,
        "data_quality": dq,
        "notes": notes,
        "method": METHOD,
    }


def level_at_least(level: str, floor: str) -> bool:
    return LEVELS.index(level) >= LEVELS.index(floor)
