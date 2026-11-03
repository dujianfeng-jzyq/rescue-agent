"""实时红线报警（由 mcp/fire-model 的 alarms.py 迁移，判据不变）。

报警是“立即行动”的触发条件，与预警等级独立判定，报警不会抬高等级。
"""

from __future__ import annotations

from typing import Any

from _evidence import sensors, series
from _quality import check_series, slope_per_hour


def check_methane(ev: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """第75条：甲烷超过限值且继续上升时，应立即撤到安全地点。

    采空区束管本身甲烷就高，这条规则针对人员可能所在的巷道测点（不含 BT- 开头的束管）。"""
    limit = cfg["limit_pct"]
    out = []
    for s in sensors(ev):
        if "CH4" not in s["measurands"] or s["id"].startswith("BT-"):
            continue
        usable = check_series(s["id"], series(ev, s["id"], "CH4", cfg["window_h"])).usable
        if not usable:
            continue
        last = usable[-1]["value"]
        rate = slope_per_hour(usable)
        if last > limit and (rate is None or rate > 0):
            out.append({
                "rule_id": "RL-075", "article": "第75条", "sensor_id": s["id"],
                "message": f"{s['name']} 甲烷 {last:.2f}% 超过 {limit}% 且在上升，人员应立即撤到安全地点",
            })  # fmt: skip
    return out


def check_reignition(ev: dict[str, Any], cfg: dict[str, Any], warnings: list[str]) -> list[dict[str, Any]]:
    """第164条相关：启封后（或封闭火区内）束管 CO 持续上升，视为复燃征兆。

    判据：窗口内 CO 上升速率 ≥ co_rate_ppm_h，且最新值 ≥ 窗口起点的 co_growth_ratio 倍。
    只对火区台账中该工作面已封闭或已启封的火区适用；从未封闭的发火区域 CO 上升属于自燃发展，
    由预警分级处理，不报“复燃”。证据里没有火区台账时照常检查（宁可多报）。
    TODO(专家): 判据和参数需按本矿火区管理经验确定。
    """
    panel = ev["panel"]
    areas = ev.get("fire_areas")
    if areas is None:
        warnings.append("证据里没有火区台账，复燃征兆按所有束管测点检查（可能多报）")
    elif not any(a.get("panel") == panel for a in areas):
        return []
    out = []
    for t in sensors(ev):
        if not t["id"].startswith(f"BT-{panel}"):
            continue
        usable = check_series(t["id"], series(ev, t["id"], "CO", cfg["window_h"])).usable
        rate = slope_per_hour(usable)
        if rate is None or len(usable) < 3:
            continue
        growth = usable[-1]["value"] / max(usable[0]["value"], 1e-6)
        if rate >= cfg["co_rate_ppm_h"] and growth >= cfg["co_growth_ratio"]:
            out.append({
                "rule_id": "RL-164", "article": "第164条", "sensor_id": t["id"],
                "message": f"{t['name']} CO 近{cfg['window_h']:g}小时以 {rate:.1f} ppm/h 上升，可能复燃，应立即重新封闭火区并撤人",
            })  # fmt: skip
    return out


def check_all(ev: dict[str, Any], cfg: dict[str, Any], warnings: list[str]) -> list[dict[str, Any]]:
    return check_methane(ev, cfg["methane"]) + check_reignition(ev, cfg["reignition"], warnings)
