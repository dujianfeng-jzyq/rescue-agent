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
    cfg: dict[str, float],
) -> dict[str, Any]:
    """比较处置前后同一测点同一参数的上升速率，判定效果。

    before / after：处置前、处置后的可用读数（已排除可疑数据）。
    markers：同测点标志气体（C2H4、C2H2）处置前后的可用读数，用于判断是否新出现。"""
    observed_h = (now - since).total_seconds() / 3600
    rate_before, rate_after = slope_per_hour(before), slope_per_hour(after)
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
    elif rate_after is None or rate_before is None:
        effect = "insufficient"
        basis.append("处置前或处置后可用读数不足，无法计算速率")
    elif rate_after <= cfg["controlled_rate"]:
        effect = "controlled"
        basis.append(f"处置后速率 {rate_after:.3f}/h ≤ {cfg['controlled_rate']}，已停止上升")
    elif rate_before > 0 and rate_after <= rate_before * cfg["slowdown_ratio"]:
        effect = "continuing"
        basis.append(f"处置后速率 {rate_after:.3f}/h ≤ 处置前 {rate_before:.3f}/h × {cfg['slowdown_ratio']}，明显减缓但仍在上升")
    else:
        effect = "worsening"
        basis.append(f"处置后速率 {rate_after:.3f}/h 未明显低于处置前 {rate_before:.3f}/h")

    def _r(v: float | None) -> float | None:
        return round(v, 3) if v is not None else None

    return {
        "since": since.isoformat(),
        "as_of": now.isoformat(),
        "observed_h": round(observed_h, 1),
        "value_at_since": _r(before[-1].value if before else (after[0].value if after else None)),
        "latest": _r(after[-1].value if after else None),
        "change_since": _r(after[-1].value - before[-1].value) if after and before else None,
        "rate_before_per_hour": _r(rate_before),
        "rate_after_per_hour": _r(rate_after),
        "new_marker_gases": new_markers,
        "effect": effect,
        "effect_name": EFFECT_NAMES[effect],
        "basis": basis,
        "method": "处置前后速率比较（thresholds.yaml 的 effect 节）。判据为演示占位值，需按本矿制度审定",
    }
