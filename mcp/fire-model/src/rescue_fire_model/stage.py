"""自燃发展阶段与处置效果评估。

阶段和效果原来由 Agent 按技能里的文字自行判断（并据此推算煤温），现在由这里按 thresholds.yaml 的
stage、effect 两节确定性给出，Agent 照抄。判据和数值都是演示占位，需专家按本矿实验数据和制度审定。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rescue_contract.models import Reading

from .warning import LABELS, slope_per_hour

STAGES = ["unknown", "no_sign", "slow_oxidation", "accelerating", "intense"]
STAGE_NAMES = {
    "unknown": "无法判定（束管 CO 数据不可用）",
    "no_sign": "未见自燃征兆",
    "slow_oxidation": "缓慢氧化",
    "accelerating": "加速氧化",
    "intense": "激烈氧化，接近明火",
}
EFFECT_NAMES = {
    "insufficient": "观测时间不足，暂不下结论",
    "controlled": "已受控",
    "continuing": "需继续处置",
    "worsening": "恶化",
}


def classify_stage(ind: dict[str, float | None], cfg: dict[str, dict[str, float]]) -> tuple[str, list[str]]:
    """按指标判定阶段，返回（阶段，依据）。自高到低，满足该阶段任一条件即是。"""
    for name in ("intense", "accelerating", "slow_oxidation"):
        hits = [f"{LABELS[k]} {ind[k]:.3f} ≥ {v}" for k, v in cfg.get(name, {}).items() if ind.get(k) is not None and ind[k] >= v]
        if hits:
            return name, hits
    if ind.get("tube_co_ppm") is None:
        return "unknown", ["没有可用的束管 CO 数据"]
    return "no_sign", ["各阶段条件均未满足"]


def stage_dict(stage: str, basis: list[str]) -> dict[str, Any]:
    return {"stage": stage, "stage_name": STAGE_NAMES[stage], "stage_basis": basis}


def evaluate(
    since: datetime,
    now: datetime,
    before: list[Reading],
    after: list[Reading],
    markers: dict[str, tuple[list[Reading], list[Reading]]],
    stage_cfg: dict[str, dict[str, float]],
    cfg: dict[str, Any],
    flat: float = 0.0,
) -> dict[str, Any]:
    """比较处置前后同一测点同一参数的上升速率，判定效果。

    before / after：处置前、处置后的可用读数（已排除可疑数据）。
    markers：同测点标志气体（C2H4、C2H2）处置前后的可用读数，用于判断是否新出现。
    flat：|速率| 小于它按 0 处理（噪声带）；输出里的速率仍是原值。"""
    observed_h = (now - since).total_seconds() / 3600
    rate_before, rate_after = slope_per_hour(before), slope_per_hour(after)

    def eff(v: float | None) -> float | None:
        return None if v is None else (0.0 if abs(v) < flat else v)

    rb, ra = eff(rate_before), eff(rate_after)
    basis: list[str] = []

    detect = {"C2H4": stage_cfg.get("accelerating", {}).get("c2h4_ppm"), "C2H2": stage_cfg.get("intense", {}).get("c2h2_ppm")}
    new_markers = []
    for gas, (pre, post) in markers.items():
        limit = detect.get(gas)
        if limit is None or not post:
            continue
        if not any(r.value >= limit for r in pre) and any(r.value >= limit for r in post):
            new_markers.append(gas)

    if observed_h < cfg["min_observe_h"]:
        effect = "insufficient"
        basis.append(f"处置后已观测 {observed_h:.1f} 小时，少于 {cfg['min_observe_h']} 小时")
    elif new_markers:
        effect = "worsening"
        basis.append(f"处置后新出现标志气体：{'、'.join(new_markers)}")
    elif ra is None or rb is None:
        effect = "insufficient"
        basis.append("处置前或处置后可用读数不足，无法计算速率")
    elif ra <= cfg["controlled_rate"]:
        effect = "controlled"
        note = f"（|速率| < {flat} 按不变处理）" if ra == 0 and rate_after != 0 else ""
        basis.append(f"处置后速率 {rate_after:.3f}/h ≤ {cfg['controlled_rate']}{note}，已停止上升")
    elif rb > 0 and ra <= rb * cfg["slowdown_ratio"]:
        effect = "continuing"
        basis.append(f"处置后速率 {rate_after:.3f}/h ≤ 处置前 {rate_before:.3f}/h × {cfg['slowdown_ratio']}，明显减缓但仍在上升")
    else:
        effect = "worsening"
        basis.append(f"处置后速率 {rate_after:.3f}/h 未明显低于处置前 {rate_before:.3f}/h")

    def _r(v: float | None) -> float | None:
        return round(v, 3) if v is not None else None

    reduction = None
    if rate_before is not None and rate_after is not None and rate_before > 0:
        reduction = round((1 - rate_after / rate_before) * 100, 1)

    return {
        "since": since.isoformat(),
        "as_of": now.isoformat(),
        "observed_h": round(observed_h, 1),
        "value_at_since": _r(before[-1].value if before else (after[0].value if after else None)),
        "latest": _r(after[-1].value if after else None),
        "change_since": _r(after[-1].value - before[-1].value) if after and before else None,
        "rate_before_per_hour": _r(rate_before),
        "rate_after_per_hour": _r(rate_after),
        "rate_reduction_pct": reduction,  # 处置后速率比处置前降低的百分比（处置前在上升时才有）
        "flat_rate_per_hour": flat,
        "new_marker_gases": new_markers,
        "effect": effect,
        "effect_name": EFFECT_NAMES[effect],
        "basis": basis,
        "method": "处置前后速率比较（thresholds.yaml 的 effect 节）。判据为演示占位值，需按本矿制度审定",
    }


def overall(points: list[dict[str, Any]], since: datetime, now: datetime) -> dict[str, Any]:
    """汇总逐测点效果。规则（演示占位，需审定）：任一恶化→恶化；全部已受控→已受控；否则需继续处置。
    观测时间不足（insufficient）的测点单列，不参与总体结论。"""
    counts = {EFFECT_NAMES[k]: sum(p["effect"] == k for p in points) for k in ("controlled", "continuing", "worsening", "insufficient")}
    judged = [p for p in points if p["effect"] != "insufficient"]
    if not judged:
        result, basis = "insufficient", ["全部测点观测时间或数据不足"]
    elif any(p["effect"] == "worsening" for p in judged):
        result = "worsening"
        basis = ["恶化的测点：" + "、".join(f"{p['sensor_id']}/{p['measurand']}" for p in judged if p["effect"] == "worsening")]
    elif all(p["effect"] == "controlled" for p in judged):
        result, basis = "controlled", [f"参与判定的 {len(judged)} 个测点全部已受控"]
    else:
        result = "continuing"
        basis = [f"参与判定的 {len(judged)} 个测点中 {counts[EFFECT_NAMES['controlled']]} 个已受控、{counts[EFFECT_NAMES['continuing']]} 个需继续处置，无恶化"]
    return {
        "since": since.isoformat(),
        "as_of": now.isoformat(),
        "overall": result,
        "overall_name": EFFECT_NAMES[result],
        "basis": basis,
        "evaluated": len(points),
        "judged": len(judged),
        "counts": counts,
        "points": [
            {k: p[k] for k in ("sensor_id", "measurand", "effect", "effect_name", "value_at_since", "latest", "change_since", "rate_before_per_hour", "rate_after_per_hour", "rate_reduction_pct", "new_marker_gases")}
            for p in points
        ],
        "method": "逐测点处置前后速率比较后汇总（thresholds.yaml 的 effect 节；汇总规则见工具说明）。判据为演示占位值，需按本矿制度审定",
    }
