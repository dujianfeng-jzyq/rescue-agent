"""W2 效果评估、W3 升级建议（docs/workflow-design.md）。判据在 fire-model 的 thresholds.yaml（占位值）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from rescue_contract import ledger
from rescue_contract.client import MineData
from rescue_fire_model import effect, warning
from rescue_kb.server import LOCAL_ARTICLES, article_number

from .assess import level_at_least


async def review_effect(md: MineData, panel: str, since: datetime | None) -> dict[str, Any]:
    source = "指挥人员给出"
    if since is None:
        rec = ledger.last_issued("issue_firefighting_plan", panel)
        if rec is None:
            raise ToolError(f"指令台账里没有工作面 {panel} 已下发的防灭火方案，请给出处置开始时间 since（矿井时间）")
        since = ledger.issued_at(rec)
        source = f"指令台账：{rec.get('action_id')} 防灭火方案 {(rec.get('payload') or {}).get('plan_id')} 的下发时间"
    out = await effect.panel_effect(md, panel, since)
    out["since_source"] = source
    out["escalation_suggested"] = out["overall"] == "worsening"
    return out


def articles(nums: list[str]) -> list[dict[str, str]]:
    want = [article_number(n) for n in nums]
    return [
        {"document": a["document"], "article": a["article"], "content": a["text"], "source": "local-fallback"}
        for n in want
        for a in LOCAL_ARTICLES
        if a["document"] == "矿山救援规程" and article_number(a["article"]) == n
    ]


def recommend(assessment: dict[str, Any], effect_result: dict[str, Any] | None) -> dict[str, Any]:
    cfg = warning.load_thresholds()["escalation"]
    w = assessment["warning"]
    reasons = []
    if level_at_least(w["level"], cfg["level_at_least"]):
        reasons.append(f"预警等级 {w['level_name']}，达到 {warning.NAMES[cfg['level_at_least']]}")
    if w["stage"] in cfg["stages"]:
        reasons.append(f"自燃发展阶段 {w['stage_name']}")
    if cfg.get("effect_worsening") and effect_result and effect_result.get("overall") == "worsening":
        reasons.append(f"最近一次效果评估（处置开始 {effect_result['since']}）结论为 {effect_result['overall_name']}")
    if cfg.get("realtime_alarm") and assessment["realtime_alarms"]:
        reasons += [f"实时红线报警 [{a['article']}] {a['sensor_id']}：{a['message']}" for a in assessment["realtime_alarms"]]
    not_met = []
    if not reasons:
        not_met.append(f"预警等级 {w['level_name']}，未达到 {warning.NAMES[cfg['level_at_least']]}")
        not_met.append(f"自燃发展阶段 {w['stage_name']}")
        not_met.append("最近一次效果评估：" + (effect_result["overall_name"] if effect_result else "没有"))
        not_met.append("实时红线报警：无")
    return {
        "panel": assessment["panel"],
        "as_of": assessment["as_of"],
        "recommend": bool(reasons),
        "reasons": reasons,
        "not_met": not_met,
        "criteria": cfg,
        "articles": articles(cfg["articles"]),
        "evacuation_reminder": "一旦进入应急响应，撤人由调度员按预案立即执行，不等待本建议和任何研判结果。",
        "level": w["level"],
        "level_name": w["level_name"],
        "stage_name": w["stage_name"],
    }
