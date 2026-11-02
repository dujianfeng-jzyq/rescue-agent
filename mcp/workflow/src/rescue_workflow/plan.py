"""W4 方案草案：现场条件和必备步骤由代码生成（docs/workflow-design.md W4）。

现场条件只来自研判快照、监测数据、火区台账和带确认人的人工输入；智能体只能在草案上补充描述、巷道、
可选步骤和依据，不能改现场条件和必备步骤（actions 发布时按快照核对）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from rescue_contract.client import MineData
from rescue_contract.models import (
    AirwayKind,
    Citation,
    FireAreaStatus,
    FireContext,
    FireType,
    HumanInput,
    Measurand,
    Plan,
    PlanKind,
    PlanStep,
    SensorSource,
    StepType,
)
from rescue_dispatch.server import estimate_inerting
from rescue_fire_model import warning
from rescue_fire_model.quality import check_series
from rescue_kb.server import LOCAL_ARTICLES, article_number

from . import config

TITLES = {"sealing": "采空区火区封闭方案", "unsealing": "采空区火区启封方案", "firefighting": "采空区自燃防灭火方案"}
PREFIX = {"sealing": "SP", "unsealing": "UP", "firefighting": "FP"}
HUMAN_FIELDS = {
    "area_temperature_c": "灾区温度（℃）",
    "visibility_m": "能见度（米）",
    "fire_source_confirmed": "火源是否明确",
    "water_supply_sufficient": "水源、人力和物力是否充足",
    "return_airway_clear": "回风道是否畅通",
}
EDITABLE = [
    "steps[].description（措施说明）",
    "steps[].roadways（巷道，密闭位置按第86条选择并写明需现场确认）",
    "增加可选步骤：grouting、pressure_balancing、inhibitor_spray、monitor、leak_sealing（顺序号可重排）",
    "进入灾区步骤（enter_disaster_area、inspect_sealed_area）的 team_size、standby_team",
    "basis（只能照抄 kb 返回的 document、article）",
    "title",
]
FIXED = ["snapshot_id", "panel", "kind", "context（全部字段）", "必备步骤的类型和相对顺序"]


def _desc(kind: str, step: str, first: bool) -> str:
    sealing = {
        "evacuate": "撤出受影响区域人员（撤人按预案立即执行，不等待本方案）",
        "inert_injection": "封闭前向采空区注入惰性气体抑爆（第85条）",
        "temporary_seal": "先建临时密闭（第84条）；密闭位置按第86条选择，需现场侦察确认；多条巷道先支巷后主巷，进、回风同时封闭时统一指挥",
        "monitor": "观测风向、风量、烟雾和气体，确认无爆炸危险；封闭过程中可燃气体浓度迅速增加时所有人员立即撤到安全地点（第85条）",
        "permanent_seal": "确认无爆炸危险后建永久或防爆密闭（第84条）",
    }
    unsealing = {
        "monitor": "启封前检查火区温度、各种气体浓度和巷道支护（第164条）"
        if first
        else "启封后按规定每班检查，发现复燃征兆立即重新封闭（第164条）",
        "power_cutoff": "切断回风流电源（第164条）",
        "evacuate": "撤出回风侧人员，在通往回风道交叉口设栅栏和警示标志（第164条）",
        "unseal": "锁风、逐段恢复通风",
    }
    firefighting = {
        "leak_sealing": "回风侧堵漏，减少向采空区漏风",
        "inert_injection": "向采空区高温区域注氮降氧",
        "monitor": "按本矿自然发火预测预报制度的频次加密束管取样，观测处置效果（频次由指挥部确定）",
    }
    return {"sealing": sealing, "unsealing": unsealing, "firefighting": firefighting}[kind][step]


async def methane_context(md: MineData, panel: str, cfg: dict[str, Any]) -> tuple[float | None, bool | None, list[str]]:
    """灾区甲烷：本工作面人员可能所在巷道的安全监控测点（非束管）的最新可用值取最大；任一测点速率超阈值为“继续上升”。"""
    now = await md.now()
    sensors = [s for s in await md.sensors() if panel in s.id and s.source != SensorSource.BUNDLE_TUBE and Measurand.CH4 in s.measurands]
    if not sensors:
        return None, None, [f"工作面 {panel} 没有巷道甲烷测点，甲烷浓度缺失"]
    rs = await md.readings([s.id for s in sensors], start=now - timedelta(hours=cfg["methane_window_h"]), end=now, measurands=["CH4"])
    latest: list[float] = []
    rates: list[float] = []
    notes: list[str] = []
    for s in sensors:
        rep = check_series(f"{s.id}/CH4", sorted((r for r in rs if r.sensor_id == s.id), key=lambda r: r.ts))
        notes += rep.flags
        if rep.usable:
            latest.append(rep.usable[-1].value)
            if (rate := warning.slope_per_hour(rep.usable)) is not None:
                rates.append(rate)
    pct = round(max(latest), 3) if latest else None
    rising = None if not rates else any(r > cfg["methane_rising_rate_pct_h"] for r in rates)
    return pct, rising, notes


async def sealed_at(md: MineData, panel: str):
    areas = [a for a in await md.fire_areas() if a.panel == panel and a.status == FireAreaStatus.SEALED]
    return max((a.sealed_at for a in areas), default=None)


def _basis(kind: str, cfg: dict[str, Any]) -> list[Citation]:
    want = [article_number(a) for a in cfg["basis"][kind]]
    return [
        Citation(document=a["document"], article=a["article"], source="local-fallback")
        for n in want
        for a in LOCAL_ARTICLES
        if a["document"] == "矿山救援规程" and article_number(a["article"]) == n
    ]


async def draft(
    md: MineData,
    kind: str,
    panel: str,
    assessment: dict[str, Any],
    human_inputs: list[HumanInput],
    leak_m3_min: float | None,
    target_o2_pct: float | None,
) -> dict[str, Any]:
    if kind not in TITLES:
        raise ToolError(f"kind 取 {' / '.join(TITLES)}")
    cfg = config.load()["plan"]
    ccfg = warning.load_thresholds()["context"]
    seen: set[str] = set()
    for h in human_inputs:
        if h.field in seen:
            raise ToolError(f"human_inputs 里 {h.field} 重复")
        seen.add(h.field)
        if h.field in ("area_temperature_c", "visibility_m") and isinstance(h.value, bool):
            raise ToolError(f"{h.field} 应为数值")
        if h.field not in ("area_temperature_c", "visibility_m") and not isinstance(h.value, bool):
            raise ToolError(f"{h.field} 应为 true 或 false")

    methane, rising, notes = await methane_context(md, panel, ccfg)
    xr = assessment["explosion"]
    worst = next((p for p in xr["points"] if p["sensor_id"] == xr["worst_sensor_id"]), None)
    o2 = worst["sample"]["readings"].get("O2") if worst else None
    human = {h.field: h.value for h in human_inputs}
    ctx = FireContext(
        fire_type=FireType.GOAF_SPONTANEOUS,
        as_of=assessment["as_of"],
        methane_pct=methane,
        methane_rising=rising,
        o2_pct=o2,
        explosion_risk=xr["overall"],
        sealed_at=await sealed_at(md, panel),
        **{k: (float(v) if k in ("area_temperature_c", "visibility_m") else v) for k, v in human.items()},
    )

    required = cfg["required_steps"][kind]
    net = await md.network()
    ret = next((r.id for r in net.roadways if r.kind == AirwayKind.RETURN and panel in r.id), None)
    threatened = [g["roadway_id"] for g in (assessment.get("personnel") or {}).get("threatened", [])]
    missing: list[str] = [
        f"{HUMAN_FIELDS[f]}（{f}）：系统没有数据，需现场确认后由 human_inputs 传入并写明确认人" for f in HUMAN_FIELDS if f not in human
    ]
    steps = []
    inert_calc = None
    for i, t in enumerate(required):
        params: dict[str, Any] = {}
        roadways: list[str] = []
        if t == "evacuate" and kind == "sealing":
            roadways = threatened
        if t == "leak_sealing" and ret:
            roadways = [ret]
        if t == "inert_injection":
            params["gas"] = "N2"
            if leak_m3_min is not None and target_o2_pct is not None and o2 is not None:
                try:
                    inert_calc = estimate_inerting(leak_m3_min, o2, target_o2_pct)
                except ValueError as exc:
                    raise ToolError(str(exc)) from exc
                params["rate_m3_h"] = inert_calc["required_n2_m3_h"]
            else:
                missing.append("注氮流量：给出漏风量 leak_m3_min 和目标氧浓度 target_o2_pct 后由代码估算（dispatch 公式），不要自己算")
        steps.append(
            PlanStep(
                order=i + 1,
                type=StepType(t),
                description=_desc(kind, t, first=(t == "monitor" and i == 0)),
                roadways=roadways,
                params=params,
            )
        )
    if not threatened and kind == "sealing":
        missing.append("撤人巷道：研判快照未计算受威胁人员，撤人范围按预案确定")
    plan = Plan(
        plan_id=f"{PREFIX[kind]}-{panel}-{ctx.as_of:%Y%m%d%H%M}",
        kind=PlanKind(kind),
        title=f"{panel} {TITLES[kind]}",
        context=ctx,
        steps=steps,
        basis=_basis(kind, cfg),
        panel=panel,
    )
    return {
        "panel": panel,
        "as_of": assessment["as_of"],
        "plan": plan.model_dump(mode="json"),
        "required_steps": required,
        "editable": EDITABLE,
        "fixed": FIXED,
        "missing_inputs": missing,
        "human_inputs": [h.model_dump(mode="json") for h in human_inputs],
        "context_sources": {
            "methane_pct": f"工作面 {panel} 巷道安全监控测点最近 {ccfg['methane_window_h']} 小时最新可用 CH4 的最大值",
            "methane_rising": f"任一测点速率 > {ccfg['methane_rising_rate_pct_h']} %/h 为 true；算不出速率为 null",
            "o2_pct": f"爆炸危险性最不利测点 {xr['worst_sensor_id']} 的 O2" if worst else "无可用束管测点，缺失",
            "explosion_risk": "研判快照的爆炸危险性汇总（多测点取最不利）",
            "sealed_at": "火区台账中本工作面已封闭火区的封闭时间",
            "人工确认项": "只来自 human_inputs（写明确认人）",
        },
        "inert_estimate": inert_calc,
        "data_quality": notes,
    }
