"""处置效果的判定与汇总。由 mcp/fire-model 的 stage.py（evaluate、overall）迁移，判据和输出不变。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from _quality import slope_per_hour

EFFECT_NAMES = {
    "insufficient": "观测时间不足，暂不下结论",
    "controlled": "已受控",
    "continuing": "需继续处置",
    "worsening": "恶化",
}


def evaluate(
    since: datetime, now: datetime, before, after, markers, cfg: dict[str, Any], flat: float = 0.0, min_increase: float | None = None
) -> dict[str, Any]:
    """比较处置前后同一测点同一参数的上升速率，判定效果。before / after 是可用读数（已排除可疑数据）；
    markers：同测点标志气体处置前后的可用读数，用于判断是否新出现；flat：|速率| 小于它按 0 处理；
    min_increase：未满观测期时提前判恶化要求速率至少加快多少（每小时），None 不提前判。"""
    observed_h = (now - since).total_seconds() / 3600
    rate_before, rate_after = slope_per_hour(before), slope_per_hour(after)

    def eff(v):
        return None if v is None else (0.0 if abs(v) < flat else v)

    rb, ra = eff(rate_before), eff(rate_after)
    basis: list[str] = []
    new_markers = []
    for gas, (pre, post) in markers.items():
        limit = cfg["markers"].get(gas)
        if limit is None or not post:
            continue
        if not any(r["value"] >= limit for r in pre) and any(r["value"] >= limit for r in post):
            new_markers.append(gas)

    # 坏消息早报，好消息等满观测期（2026-11-12 演示：未处置 9.5 小时，束管新出现 C2H4、CO 速率是起点前的 7 倍，
    # 原来先判观测时长，结论却是“观测时间不足”）：新出现标志气体随时判恶化；速率明显加快观测满 early.min_observe_h 就判恶化；
    # 已受控、需继续处置仍要观测满 min_observe_h。
    early = cfg["early_worsening"]
    faster = (
        min_increase is not None
        and ra is not None
        and rb is not None
        and ra > 0
        and ra >= max(rb, 0.0) * early["rate_ratio"]
        and ra - max(rb, 0.0) >= min_increase
    )
    if new_markers:
        effect = "worsening"
        basis.append(f"处置后新出现标志气体：{'、'.join(new_markers)}")
    elif observed_h < cfg["min_observe_h"]:
        if faster and observed_h >= early["min_observe_h"]:
            effect = "worsening"
            basis.append(
                f"处置后已观测 {observed_h:.1f} 小时（未满 {cfg['min_observe_h']} 小时），速率 {rate_after:.3f}/h ≥ 处置前 {rate_before:.3f}/h × {early['rate_ratio']}，"
                f"且加快不少于 {min_increase}/h，明显加快"
            )
        else:
            effect = "insufficient"
            basis.append(f"处置后已观测 {observed_h:.1f} 小时，少于 {cfg['min_observe_h']} 小时")
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

    def _r(v):
        return round(v, 3) if v is not None else None

    reduction = None
    if rate_before is not None and rate_after is not None and rate_before > 0:
        reduction = round((1 - rate_after / rate_before) * 100, 1)
    return {
        "value_at_since": _r(before[-1]["value"] if before else (after[0]["value"] if after else None)),
        "latest": _r(after[-1]["value"] if after else None),
        "change_since": _r(after[-1]["value"] - before[-1]["value"]) if after and before else None,
        "rate_before_per_hour": _r(rate_before),
        "rate_after_per_hour": _r(rate_after),
        "rate_reduction_pct": reduction,
        "new_marker_gases": new_markers,
        "effect": effect,
        "effect_name": EFFECT_NAMES[effect],
        "basis": basis,
    }


def overall(points: list[dict[str, Any]], since: datetime, now: datetime, review_interval_h: float) -> dict[str, Any]:
    """汇总逐测点效果：任一恶化→恶化；全部已受控→已受控；否则需继续处置。观测时间不足的测点不参与总体结论。"""
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
        "observed_h": round((now - since).total_seconds() / 3600, 1),
        "next_review_at": (now + timedelta(hours=review_interval_h)).isoformat(),
        "observed_h_at_next_review": round((now - since).total_seconds() / 3600 + review_interval_h, 1),
        "overall": result,
        "overall_name": EFFECT_NAMES[result],
        "basis": basis,
        "evaluated": len(points),
        "judged": len(judged),
        "counts": counts,
    }
