#!/usr/bin/env python3
"""生成第二版火灾闭环技能（fire-assessment、explosion-triangle、emergency-escalation、sealing-plan、plan-compliance、effect-review）的自检样例。

输入取自模拟矿井的标准场景（手动时钟，Python 采集小工具按 fire-assessment 的采集清单取证据），
期望输出是当前脚本的运行结果（金标准；关键字段与第一版服务的一致性在 P1 用当时的对照测试验证过（已随第一版包删除））。

    uv run --project mcp python scripts/make_skill_cases.py            # 重新生成全部样例的输入和期望
    uv run --project mcp python scripts/make_skill_cases.py --expected # 只按现有输入重写期望（改了参数或算法后）

改参数后重写期望，提交说明里写明是占位值还是专家审定值（skills/README.md）。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

from rescue_contract.collect import collect_from, parse_collect_spec
from rescue_contract.skills import load_skill, run_script
from rescue_contract.template import render, report_data
from rescue_mine_data_sim.server import SimHolder, build_server

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
FA, XT, ESC = "fire-assessment", "explosion-triangle", "emergency-escalation"
SP, PC, ER = "sealing-plan", "plan-compliance", "effect-review"
IR = "incident-review"
UP = "unsealing-plan"
FP = "firefighting-plan"


def evidence(scenario: str, hours: float, kind: str = "assessment", unseal_at: float | None = None, skill: str = FA) -> dict:
    holder = SimHolder(scenario, manual_clock=True)
    if unseal_at is not None:
        holder.engine.clock.advance(unseal_at)
        holder.engine.apply("unseal", None, source="eval")
        hours -= unseal_at
    holder.engine.clock.advance(hours)
    spec = parse_collect_spec((SKILLS / skill / "references" / "collect.yaml").read_text(encoding="utf-8"))
    return asyncio.run(collect_from(build_server(holder), spec, kind, "1201"))


def run(skill: str, script: str, data: dict) -> dict:
    r = run_script(load_skill(SKILLS / skill).scripts[script], data)
    if not r.ok:
        raise SystemExit(f"{skill}/{script}：{r.status} {r.detail}")
    return r.output


def stale_evidence(base: dict, group: str, sensor_id: str, hours: float) -> dict:
    """某测点最近 hours 小时没有新数据（去掉这段读数，最新值取剩下的最后一个）。"""
    from datetime import datetime, timedelta

    ev = copy.deepcopy(base)
    cut = datetime.fromisoformat(ev["as_of"]) - timedelta(hours=hours)
    for g in ev["groups"].values():
        if sensor_id not in g["measurands"]:
            continue
        g["readings"] = [r for r in g["readings"] if r["sensor_id"] != sensor_id or datetime.fromisoformat(r["ts"]) <= cut]
        last = {}
        for r in g["readings"]:
            if r["sensor_id"] == sensor_id:
                last[r["measurand"]] = r
        g["latest"] = sorted([r for r in g["latest"] if r["sensor_id"] != sensor_id] + list(last.values()), key=lambda r: (r["sensor_id"], r["measurand"], r["ts"]))
    return ev


def methane_evidence(base: dict) -> dict:
    """在正常场景的证据上把上隅角甲烷改成超限且上升（标准场景里没有第75条报警的时刻）。"""
    ev = copy.deepcopy(base)
    g = ev["groups"]["methane"]
    rs = sorted((r for r in g["readings"] if r["sensor_id"] == "SM-1201-UC"), key=lambda r: r["ts"])
    for i, r in enumerate(rs):
        r["value"] = round(2.1 + 0.1 * i, 3)
    ev["fire_areas"] = []
    ev["groups"] = {"methane": g}
    return ev


def build_inputs() -> dict[tuple[str, str, str], dict]:
    s4_45, s4_60 = evidence("s4-escalation", 45), evidence("s4-escalation", 60)
    s1_24, s6_36 = evidence("s1-normal", 24, "check"), evidence("s6-sensor-faults", 36, "check")
    s5_312 = evidence("s5-sealed-fire-area", 312, "check", unseal_at=240)
    w = {k: run(FA, "warning", {"evidence": ev}) for k, ev in (("s4-60", s4_60), ("s1-24", s1_24), ("s5-312", s5_312))}
    st = {k: run(FA, "stage", {"warning": v}) for k, v in w.items()}
    al = {k: run(FA, "alarms", {"evidence": ev}) for k, ev in (("s4-60", s4_60), ("s1-24", s1_24), ("s5-312", s5_312))}
    resealed = copy.deepcopy(s5_312)  # 同一份证据，火区台账改成已封闭：CO 上升仍报警，但不触发“重新封闭”
    for a in resealed["fire_areas"]:
        a["status"], a["unsealed_at"] = "sealed", None
    no_register = {k: v for k, v in s5_312.items() if k != "fire_areas"}  # 没有火区台账：不下“重新封闭”的结论
    intense = copy.deepcopy(w["s4-60"])
    intense["result"]["values"]["c2h2_ppm"] = 0.2
    return {
        (FA, "warning", "s4-escalation-60h-red"): {"evidence": s4_60},
        (FA, "warning", "s6-sensor-faults-36h"): {"evidence": s6_36},
        (FA, "stage", "s4-escalation-60h-accelerating"): {"warning": w["s4-60"]},
        (FA, "stage", "s1-normal-24h-no-sign"): {"warning": w["s1-24"]},
        (FA, "stage", "intense-c2h2"): {"warning": intense},
        (FA, "alarms", "s5-unsealed-312h-reignition"): {"evidence": s5_312},
        (FA, "alarms", "s5-sealed-312h-co-rising-not-reseal"): {"evidence": resealed},
        (FA, "alarms", "s5-312h-no-fire-register"): {"evidence": no_register},
        (FA, "alarms", "methane-over-limit-rising"): {"evidence": methane_evidence(s1_24)},
        (FA, "trend", "s4-escalation-60h"): {"evidence": s4_60, "warning": w["s4-60"]},
        (FA, "personnel", "s4-escalation-60h-red"): {"evidence": s4_60, "warning": w["s4-60"]},
        (FA, "personnel", "s1-normal-not-computed"): {"evidence": {**s1_24, "groups": {}}, "warning": w["s1-24"]},
        (FA, "check", "s1-normal-24h-no-anomaly"): {"evidence": s1_24},
        (FA, "check", "s6-sensor-faults-36h-failed"): {"evidence": s6_36},
        (FA, "check", "s4-escalation-60h-vs-45h"): {"evidence": s4_60, "baseline": run(FA, "check", {"evidence": s4_45})},
        (FA, "check", "s1-normal-24h-data-gap"): {"evidence": stale_evidence(s1_24, "tubes", "BT-1201-G2", 4)},
        (XT, "explosion_triangle", "s6-sensor-faults-36h-panel"): {"evidence": s6_36},
        (XT, "explosion_triangle", "s1-normal-24h-g1-zone-ii"): {"evidence": s1_24, "options": {"sensor_id": "BT-1201-G1"}},
        (XT, "explosion_triangle", "manual-sample"): {
            "sample": {"o2_pct": 8.2, "co2_pct": 3.1, "temperature_c": 32.0, "ch4_pct": 4.5, "co_ppm": 120.0, "c2h4_ppm": 0.8}
        },
        (XT, "explosion_triangle", "manual-sample-zone-i"): {
            "sample": {"o2_pct": 15.0, "co2_pct": 1.0, "n2_pct": 76.0, "temperature_c": 25.0, "ch4_pct": 8.0}
        },
        (ESC, "escalation", "s4-escalation-60h-recommend"): {"warning": w["s4-60"], "stage": st["s4-60"], "alarms": al["s4-60"]},
        (ESC, "escalation", "s1-normal-24h-not-met"): {"warning": w["s1-24"], "stage": st["s1-24"], "alarms": al["s1-24"]},
        (ESC, "escalation", "s5-unsealed-312h-alarm"): {"warning": w["s5-312"], "stage": st["s5-312"], "alarms": al["s5-312"]},
        (FA, "explanation_options", "s4-escalation-60h-red"): flow_results(s4_60),
        (FA, "explanation_options", "s6-sensor-faults-36h"): flow_results(s6_36),
        (FA, "explanation_options", "s1-normal-24h"): flow_results(s1_24),
    }


# 封闭方案：“方案编写”“合规审核”两个阶段的选择是模型给的，样例里写死
SEAL_FULL = {
    "optional_steps": ["leak_sealing", "grouting", "monitor_after_seal"],
    "seal_roadways": ["R-1201-IN", "R-1201-RET"],
    "step_notes": [{"key": "temporary_seal", "note": "进、回风同时封闭，统一指挥"}, {"key": "monitor", "note": ""}],
    "summary": "先注惰抑爆，再封闭进、回风两侧",
}
SEAL_MINIMAL = {"optional_steps": [], "seal_roadways": ["R-1201-RET"], "step_notes": [], "summary": ""}
REDLINE_OK = {"ok": True, "problems": [], "notes": []}
REDLINE_FAIL = {"ok": False, "problems": ["红线校验未通过（1 项）：\n- [第85条] 步骤3：火区存在爆炸危险，封闭前必须先注入惰性气体等抑爆，方案缺少前置注惰步骤。"], "notes": []}


def review_choice(scope: dict, fail: str | None = None, human: str | None = None) -> dict:
    items = []
    for it in scope["result"]["items"]:
        c = "fail" if it["id"] == fail else "needs_human" if it["id"] == human else "pass"
        items.append({"id": it["id"], "conclusion": c, "articles": ["第84条"] if it["id"] == "sequence" else [], "note": "密闭位置需现场确认" if c != "pass" else ""})
    return {"items": items, "summary": "按必备步骤先注惰后封闭"}


def sealing_inputs() -> dict[tuple[str, str, str], dict]:
    s4 = evidence("s4-escalation", 60, "plan", skill=SP)
    s1 = evidence("s1-normal", 24, "plan", skill=SP)
    pre = {}
    for k, ev in (("s4", s4), ("s1", s1)):
        w = run(FA, "warning", {"evidence": ev})
        pre[k] = {
            "evidence": ev,
            "explosion": run(XT, "explosion_triangle", {"evidence": ev}),
            "personnel": run(FA, "personnel", {"evidence": ev, "warning": w}),
        }
    humans = [
        {"field": "area_temperature_c", "value": 35, "confirmed_by": "救护队带队指挥员"},
        {"field": "return_airway_clear", "value": True, "confirmed_by": "通风部门负责人", "note": "现场侦察"},
    ]
    sk_full = {**pre["s4"], "options": {"human_inputs": humans, "leak_m3_min": 30, "target_o2_pct": 5}}
    sk_plain = {**pre["s4"], "options": {"human_inputs": []}}
    sk_s1 = {**pre["s1"], "options": {"human_inputs": []}}
    out = {
        (SP, "skeleton", "s4-escalation-60h-human-inputs"): sk_full,
        (SP, "skeleton", "s4-escalation-60h"): sk_plain,
        (SP, "skeleton", "s1-normal-24h-no-personnel"): sk_s1,
    }
    full = {"skeleton": run(SP, "skeleton", sk_full), "evidence": s4, "options": {"selection": SEAL_FULL}}
    minimal = {"skeleton": run(SP, "skeleton", sk_plain), "evidence": s4, "options": {"selection": SEAL_MINIMAL}}
    out[(SP, "compose", "s4-escalation-60h-full")] = full
    out[(SP, "compose", "s4-escalation-60h-minimal")] = minimal
    plan = run(SP, "compose", full)
    out[(PC, "scope", "s4-escalation-60h-full")] = {"plan": plan}
    scope = run(PC, "scope", {"plan": plan})
    out[(PC, "review", "pass")] = {"plan": plan, "scope": scope, "options": {"redline": REDLINE_OK, "review": review_choice(scope), "round": 1}}
    out[(PC, "review", "item-fail")] = {"plan": plan, "scope": scope, "options": {"redline": REDLINE_OK, "review": review_choice(scope, fail="site_survey"), "round": 1}}
    out[(PC, "review", "redline-fail-needs-human")] = {
        "plan": plan, "scope": scope, "options": {"redline": REDLINE_FAIL, "review": review_choice(scope, human="context"), "round": 2},
    }  # fmt: skip
    return out


def effect_evidence(scenario: str, at: float, actions: list, then: float) -> dict:
    """处置后复评的证据：在 at 小时执行处置（同 eval/agent/cases.yaml 的 apply），then 小时按 effect 清单采集。"""
    holder = SimHolder(scenario, manual_clock=True)
    holder.engine.clock.advance(at)
    since = holder.engine.now().isoformat()
    for t, prm, rw in actions:
        holder.engine.apply(t, prm, roadways=rw, source="eval")
    holder.engine.clock.advance(then - at)
    spec = parse_collect_spec((SKILLS / ER / "references" / "collect.yaml").read_text(encoding="utf-8"))
    return asyncio.run(collect_from(build_server(holder), spec, "effect", "1201", since))


def effect_inputs() -> dict[tuple[str, str, str], dict]:
    inert = [("inert_injection", {"rate_m3_h": 1000}, None), ("leak_sealing", {"reduction": 0.5}, ["R-1201-RET"])]
    return {
        (ER, "effect", "s2-inerting-48h-to-72h-continuing"): {"evidence": effect_evidence("s2-slow-oxidation", 48, inert, 72), "options": {"since_source": "指挥人员给出"}},
        (ER, "effect", "s2-inerting-48h-to-54h-insufficient"): {"evidence": effect_evidence("s2-slow-oxidation", 48, inert[:1], 54)},
        (ER, "effect", "s3-inerting-40h-to-64h-worsening"): {"evidence": effect_evidence("s3-accelerating", 40, [("inert_injection", {"rate_m3_h": 600}, None)], 64)},
    }


def effect_template_inputs(inputs: dict) -> dict[tuple[str, str, str], dict]:
    out = {}
    for case in ("s2-inerting-48h-to-72h-continuing", "s3-inerting-40h-to-64h-worsening"):
        r = run(ER, "effect", inputs[(ER, "effect", case)])
        out[(ER, "report", case)] = report_data({"effect": fake_record("effect", r, 0)})
        out[(ER, "brief", case)] = report_data({"effect": fake_record("effect", r, 0)})
    return out


def sealing_template_inputs(inputs: dict) -> dict[tuple[str, str, str], dict]:
    out = {}
    for case in ("s4-escalation-60h-full", "s4-escalation-60h-minimal"):
        plan = run(SP, "compose", inputs[(SP, "compose", case)])
        refs = plan["result"]["plan"]["basis"]
        out[(SP, "plan", case)] = report_data({"compose": fake_record("compose", plan, 0)}, {"articles": index_articles([{"document": b["document"], "article": b["article"]} for b in refs])})
    plan = run(SP, "compose", inputs[(SP, "compose", "s4-escalation-60h-full")])
    rev = run(PC, "review", inputs[(PC, "review", "pass")])
    steps = "；".join(f"{x['order']}. {x['type_name']}" for x in plan["result"]["steps_view"])
    out[(SP, "brief", "s4-escalation-60h-full-pass")] = report_data({"compose": fake_record("compose", plan, 0)}, { "steps_text": steps, "redline_text": "通过", "verdict_text": "合规审核通过（1 轮）",
        "human_count": len(rev["result"]["human_items"]) or None, "review_id": "RS-20261104T200000-case01",
    })  # fmt: skip
    out[(SP, "brief", "s4-escalation-60h-minimal-no-review")] = report_data({"compose": fake_record("compose", run(SP, "compose", inputs[(SP, "compose", "s4-escalation-60h-minimal")]), 0)}, { "steps_text": "1. 撤人", "redline_text": "通过", "verdict_text": "合规审核未完成（2 轮）", "human_count": None, "review_id": None,
    })  # fmt: skip
    for case in ("pass", "redline-fail-needs-human"):
        data = inputs[(PC, "review", case)]
        rev = run(PC, "review", data)
        out[(PC, "review", case)] = report_data({"review": fake_record("review", rev, 0)}, {"articles": index_articles(data["scope"]["result"]["articles"])})
    return out


# 防灭火方案：“方案编写”阶段的选择是模型给的，样例里写死（没有密闭巷道）
FIRE_FULL = {
    "optional_steps": ["leak_sealing", "grouting", "monitor_after"],
    "step_notes": [{"key": "inert_injection", "note": "注氮口位置按现场确定"}, {"key": "monitor", "note": ""}],
    "summary": "先注惰降氧，辅以堵漏和注浆，不封闭火区",
}
FIRE_MINIMAL = {"optional_steps": [], "step_notes": [], "summary": ""}


def firefighting_review_choice(scope: dict, fail: str | None = None) -> dict:
    items = [{"id": it["id"], "conclusion": "fail" if it["id"] == fail else "pass", "articles": ["第98条"] if it["id"] == "ventilation" else [], "note": "改为不涉及反风" if it["id"] == fail else ""} for it in scope["result"]["items"]]
    return {"items": items, "summary": "按必备步骤注惰并监测"}


def firefighting_inputs() -> dict[tuple[str, str, str], dict]:
    s3 = evidence("s3-accelerating", 39, "plan", skill=FP)  # 橙色
    s1 = evidence("s1-normal", 24, "plan", skill=FP)
    pre = {k: {"evidence": ev, "explosion": run(XT, "explosion_triangle", {"evidence": ev})} for k, ev in (("s3", s3), ("s1", s1))}
    sk_full = {**pre["s3"], "options": {"leak_m3_min": 30, "target_o2_pct": 5}}
    sk_plain = {**pre["s3"], "options": {}}
    sk_s1 = {**pre["s1"], "options": {}}
    out = {
        (FP, "skeleton", "s3-accelerating-39h-inert-rate"): sk_full,
        (FP, "skeleton", "s3-accelerating-39h"): sk_plain,
        (FP, "skeleton", "s1-normal-24h"): sk_s1,
    }
    full = {"skeleton": run(FP, "skeleton", sk_full), "evidence": s3, "options": {"selection": FIRE_FULL}}
    minimal = {"skeleton": run(FP, "skeleton", sk_plain), "evidence": s3, "options": {"selection": FIRE_MINIMAL}}
    out[(FP, "compose", "s3-accelerating-39h-full")] = full
    out[(FP, "compose", "s3-accelerating-39h-minimal")] = minimal
    plan = run(FP, "compose", full)
    out[(PC, "scope", "firefighting-s3-39h-full")] = {"plan": plan}
    scope = run(PC, "scope", {"plan": plan})
    out[(PC, "review", "firefighting-pass")] = {"plan": plan, "scope": scope, "options": {"redline": REDLINE_OK, "review": firefighting_review_choice(scope), "round": 1}}
    out[(PC, "review", "firefighting-item-fail")] = {"plan": plan, "scope": scope, "options": {"redline": REDLINE_OK, "review": firefighting_review_choice(scope, fail="ventilation"), "round": 1}}
    return out


def firefighting_template_inputs(inputs: dict) -> dict[tuple[str, str, str], dict]:
    out = {}
    for case in ("s3-accelerating-39h-full", "s3-accelerating-39h-minimal"):
        plan = run(FP, "compose", inputs[(FP, "compose", case)])
        refs = plan["result"]["plan"]["basis"]
        out[(FP, "plan", case)] = report_data({"compose": fake_record("compose", plan, 0)}, {"articles": index_articles([{"document": b["document"], "article": b["article"]} for b in refs])})
    plan = run(FP, "compose", inputs[(FP, "compose", "s3-accelerating-39h-full")])
    steps = "；".join(f"{x['order']}. {x['type_name']}" for x in plan["result"]["steps_view"])
    out[(FP, "brief", "s3-accelerating-39h-full-pass")] = report_data({"compose": fake_record("compose", plan, 0)}, {"steps_text": steps, "redline_text": "通过", "verdict_text": "合规审核通过（1 轮）", "human_count": None, "review_id": "RS-20261104T200000-case01"})  # fmt: skip
    data = inputs[(PC, "review", "firefighting-pass")]
    rev = run(PC, "review", data)
    out[(PC, "review", "firefighting-pass")] = report_data({"review": fake_record("review", rev, 0)}, {"articles": index_articles(data["scope"]["result"]["articles"])})
    return out


def flow_results(ev: dict) -> dict:
    """研判流程各脚本的输出（explanation_options 的输入）。"""
    r = {"warning": run(FA, "warning", {"evidence": ev})}
    r["stage"] = run(FA, "stage", {"warning": r["warning"]})
    r["alarms"] = run(FA, "alarms", {"evidence": ev})
    r["trend"] = run(FA, "trend", {"evidence": ev, "warning": r["warning"]})
    r["explosion"] = run(XT, "explosion_triangle", {"evidence": ev})
    r["personnel"] = run(FA, "personnel", {"evidence": ev if "network" in ev else {**ev, "groups": {}}, "warning": r["warning"]})
    r["escalation"] = run(ESC, "escalation", {k: r[k] for k in ("warning", "stage", "alarms")})
    return r


def fake_record(name: str, content: dict, i: int) -> dict:
    """样例用的记录：编号是假的（格式合法），只用于渲染。"""
    r = content.get("result", {})
    as_of = (r.get("as_of") or content.get("as_of") or "2026-11-04T00:00:00")[:19]
    prefix = "EV" if name == "evidence" else "RS"
    return {"record_id": f"{prefix}-{as_of.replace('-', '').replace(':', '')}-case{i:02d}", "panel": "1201", "as_of": as_of, "content": content}


def build_template_inputs(inputs: dict) -> dict[tuple[str, str, str], dict]:
    """模板样例的渲染数据：用脚本样例的输入重新运行脚本，按 renderReport 的方式组织。"""
    out = {}
    for label, ev_key, esc_key in (
        ("s4-escalation-60h-red", (FA, "warning", "s4-escalation-60h-red"), (ESC, "escalation", "s4-escalation-60h-recommend")),
        ("s1-normal-24h", (FA, "check", "s1-normal-24h-no-anomaly"), (ESC, "escalation", "s1-normal-24h-not-met")),
        ("s6-sensor-faults-36h", (FA, "warning", "s6-sensor-faults-36h"), None),
    ):
        ev = inputs[ev_key]["evidence"]
        res = {"evidence": ev}
        res["warning"] = run(FA, "warning", {"evidence": ev})
        res["stage"] = run(FA, "stage", {"warning": res["warning"]})
        res["alarms"] = run(FA, "alarms", {"evidence": ev})
        res["trend"] = run(FA, "trend", {"evidence": ev, "warning": res["warning"]})
        res["explosion"] = run(XT, "explosion_triangle", {"evidence": ev})
        ev_full = ev if "network" in ev else None
        res["personnel"] = run(FA, "personnel", {"evidence": ev_full or {**ev, "groups": {}}, "warning": res["warning"]})
        recs = {k: fake_record(k, v, i) for i, (k, v) in enumerate(res.items())}
        recs["evidence"]["as_of"] = ev["as_of"]
        chart = f"![火区混合气体爆炸三角形](records/case/{recs['explosion']['record_id']}.chart.svg)" if res["explosion"]["result"]["points"] else None
        out[(FA, "report", label)] = report_data(recs, {"chart_markdown": chart})
        if label == "s4-escalation-60h-red":
            x = res["explosion"]["result"]
            worst = next((p for p in x["points"] if p["sensor_id"] == x["worst_sensor_id"]), None)
            esc = run(ESC, "escalation", {k: res[k] for k in ("warning", "stage", "alarms")})
            brief = {k: recs[k] for k in ("warning", "stage", "alarms", "explosion", "personnel")}
            brief["escalation"] = fake_record("escalation", esc, 9)
            out[(FA, "brief", label)] = report_data(brief, {
                "worst": {k: worst[k] for k in ("sensor_id", "zone", "zone_name")} if worst else None,
                "articles_text": "、".join(f"{a['document']}{a['article']}" for a in esc["result"]["articles"]),
                "explanation_summary": "红色预警、加速氧化，建议升级为应急响应",
                "chart_markdown": chart,
            })  # fmt: skip
            for tpl in ("brief", "report"):  # 只检查模板的“重新封闭”分支：数据同 s4，仅把标志置真
                d = copy.deepcopy(out[(FA, tpl, label)])
                d["alarms"]["result"]["reseal_indicated"] = True
                out[(FA, tpl, "reseal-flag-true")] = d
        check = run(FA, "check", {"evidence": ev})
        out[(FA, "check", label)] = report_data({"check": fake_record("check", check, 0)})
        if esc_key:
            esc = run(ESC, "escalation", inputs[esc_key])
            out[(ESC, "report", label)] = report_data({"escalation": fake_record("escalation", esc, 0)}, {"articles": index_articles(esc["result"]["articles"])})
    opts = run(FA, "explanation_options", inputs[(FA, "explanation_options", "s4-escalation-60h-red")])
    fo, ns = opts["result"]["focus_options"], opts["result"]["next_step_options"]
    explanation = {
        "summary": "红色预警、加速氧化，建议升级为应急响应",
        "focus": [{"id": fo[0]["id"], "label": fo[0]["label"], "note": "束管 CO 已达红色阈值"}, {"id": fo[-1]["id"], "label": fo[-1]["label"], "note": ""}],
        "next_steps": [{"id": ns[0]["id"], "label": ns[0]["label"], "reason": "撤人不等待研判"}],
    }
    out[(FA, "explanation", "s4-escalation-60h-red")] = report_data({"options": fake_record("options", opts, 0)}, {"explanation": explanation, "child_id": "case"})
    return out


def index_articles(refs: list[dict]) -> list[dict]:
    """与知识库插件的 kb_get_articles 相同：从条文索引取原文（每段一行）。"""
    index = json.loads((ROOT / "dsh-bundle" / "data" / "article-index.json").read_text(encoding="utf-8"))
    out = []
    for ref in refs:
        n = int(ref["article"].strip("第条"))
        hit = next((a for a in index["articles"].get(ref["document"], []) if a["number"] == n), None)
        out.append({**ref, "content": "\n".join(hit["paragraphs"]) if hit else None, "source": "index" if hit else "not-found"})
    return out


def write_template_case(skill: str, template: str, case: str, data: dict) -> None:
    d = SKILLS / skill / "tests" / "templates" / template
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{case}.input.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    write_template_expected(skill, template, case, data)


def write_template_expected(skill: str, template: str, case: str, data: dict) -> None:
    tpl = (SKILLS / skill / "templates" / f"{template}.md").read_text(encoding="utf-8")
    (SKILLS / skill / "tests" / "templates" / template / f"{case}.expected.md").write_text(render(tpl, data), encoding="utf-8")


def case_dir(skill: str, script: str) -> Path:
    return SKILLS / skill / "tests" / "cases" / script


def write_expected(skill: str, script: str, case: str, data: dict) -> None:
    out = run(skill, script, data)
    (case_dir(skill, script) / f"{case}.expected.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _entry(i: int, at: str, kind: str, by: str, data: dict) -> dict:
    return {"event_id": f"EVT-{at[:10].replace('-', '')}T{at[11:].replace(':', '')}-{i:06d}", "at": at, "wall": "2026-11-05T00:00:00+00:00", "kind": kind, "by": by, "data": data}


def incident_inputs() -> dict[tuple[str, str, str], dict]:
    """事件复盘：手写的事件条目（不依赖模拟矿井）。完整过程、仍在处置中（有未完成事项）、只有告警。"""
    a1 = _entry(2, "2026-11-05T08:10:00", "alert", "sentinel", {"cause": "anomaly", "summary": "1201 工作面：预警等级由 无 升为 蓝"})
    a2 = _entry(9, "2026-11-05T12:30:00", "alert", "sentinel", {"cause": "followup_due", "summary": "跟踪计划到点：注惰后复评"})
    base = [
        _entry(1, "2026-11-05T08:00:00", "opened", "sentinel", {"category": "fire", "panel": "1201", "summary": "1201 工作面异常"}),
        a1,
        _entry(3, "2026-11-05T08:10:02", "delivered", "plugin", {"alert_id": a1["event_id"], "session_id": "S1"}),
        _entry(4, "2026-11-05T08:15:00", "note", "flow", {"type": "assessment", "text": "红色预警；加速氧化", "record_ids": ["RS-20261105T081500-aaaaaa"]}),
        _entry(5, "2026-11-05T08:20:00", "note", "guard", {"type": "action", "tool": "publish_warning", "status": "rejected", "action_id": None, "record_ids": ["RS-20261105T081500-aaaaaa"], "problems": ["预警等级与记录不一致"]}),
        _entry(6, "2026-11-05T08:25:00", "note", "guard", {"type": "action", "tool": "publish_warning", "status": "issued", "action_id": "A-1111111111", "record_ids": ["RS-20261105T081500-aaaaaa"], "problems": []}),
        _entry(7, "2026-11-05T08:30:00", "status", "agent", {"status": "handling", "reason": "开始处置"}),
        _entry(8, "2026-11-05T10:30:00", "followup_added", "agent", {"followup_id": "FU-1", "due_at": "2026-11-05T12:30:00", "expect": "注惰后复评"}),
    ]
    ev = lambda entries, **kw: {"evidence": {"incident_id": "INC-20261105T080000-aaaaaa", "category": "fire", "panel": "1201", "as_of": entries[-1]["at"], "entries": entries, **kw}}  # noqa: E731
    full = [
        *base,
        a2,
        _entry(10, "2026-11-05T12:31:00", "followup_due", "sentinel", {"followup_id": "FU-1"}),
        _entry(11, "2026-11-05T12:32:00", "delivered", "plugin", {"alert_id": a2["event_id"], "session_id": "S1"}),
        _entry(12, "2026-11-05T12:40:00", "note", "flow", {"type": "effect_review", "text": "已受控", "record_ids": ["RS-20261105T124000-bbbbbb"]}),
        _entry(13, "2026-11-05T12:41:00", "followup_done", "agent", {"followup_id": "FU-1", "result": "已受控"}),
        _entry(14, "2026-11-05T14:00:00", "recovered", "sentinel", {"summary": "1201 工作面监测指标已恢复正常"}),
        _entry(15, "2026-11-05T14:00:00", "status", "sentinel", {"status": "stable", "reason": "监测指标恢复正常"}),
        _entry(16, "2026-11-05T15:00:00", "status", "agent", {"status": "closed", "reason": "稳定"}),
    ]
    # 含人工确认的等待、超时升级、结果，以及矿井回执（一条已完成、一条执行中、一条查不到）
    approvals = [
        _entry(21, "2026-11-05T08:19:00", "note", "approval", {"type": "approval_pending", "approval_key": "c1", "tool": "publish_warning", "confirmer": "通风部门负责人", "timeout_min": 10, "escalate_to": "值班长", "record_ids": []}),
        _entry(22, "2026-11-05T08:30:00", "note", "sentinel", {"type": "approval_escalated", "approval_key": "c1", "tool": "publish_warning", "escalate_to": "值班长", "waited_min": 11}),
        _entry(23, "2026-11-05T08:35:00", "note", "approval", {"type": "approval_decided", "approval_key": "c1", "tool": "publish_warning", "outcome": "allowed-once"}),
    ]
    with_receipts = [
        *base[:4],
        *approvals,
        _entry(24, "2026-11-05T08:36:00", "note", "guard", {"type": "action", "tool": "publish_warning", "status": "issued", "action_id": "A-2222222222", "record_ids": [], "problems": [], "mine_receipt_ids": ["M-1"]}),
        _entry(25, "2026-11-05T08:40:00", "note", "guard", {"type": "action", "tool": "issue_sealing_plan", "status": "issued", "action_id": "A-3333333333", "record_ids": [], "problems": [], "mine_receipt_ids": ["M-2", "M-3"]}),
    ]
    receipts = {
        "M-1": {"action_id": "M-1", "kind": "notice", "status": "completed", "received_at": "2026-11-05T08:36:00", "updated_at": "2026-11-05T08:36:00", "idempotency_key": "k1", "detail": None},
        "M-2": {"action_id": "M-2", "kind": "treatment", "status": "executing", "received_at": "2026-11-05T08:40:00", "updated_at": "2026-11-05T08:41:00", "idempotency_key": "k2", "detail": "注惰执行中"},
        "M-3": {"error": "未知回执编号：M-3"},
    }
    return {
        (IR, "timeline", "with-approvals-and-receipts"): ev(with_receipts, receipts=receipts),
        (IR, "timeline", "full-process-closed"): ev(full),
        (IR, "timeline", "in-progress-unfinished"): ev(base),
        (IR, "timeline", "alert-only"): ev(base[:2]),
        (IR, "timeline", "approval-timeout-alert"): ev([*base[:2], _entry(3, "2026-11-05T08:40:00", "alert", "sentinel", {"cause": "approval_timeout", "summary": "publish_warning 等待人工确认已超过 10 分钟（已等 30 分钟，确认人：通风部门负责人），已提醒值班长"})]),
    }


def unseal_inputs() -> dict[tuple[str, str, str], dict]:
    """启封评估：s5 封闭火区在第 336 小时（占位判据下具备条件）、第 240 小时（封闭时长不够）、缺束管数据（无法判定）。"""
    ready = evidence("s5-sealed-fire-area", 336, "unseal", skill=UP)
    early = evidence("s5-sealed-fire-area", 240, "unseal", skill=UP)
    no_tubes = copy.deepcopy(ready)
    no_tubes["groups"]["tubes"] = {**no_tubes["groups"]["tubes"], "latest": [], "readings": []}
    hot = copy.deepcopy(ready)  # 温度超限：把采空区测温最新值改高
    for g in ("goaf_temperature",):
        for r in hot["groups"][g]["latest"] + hot["groups"][g]["readings"]:
            if r["measurand"] == "TEMP" and r["ts"] >= hot["as_of"][:13]:
                r["value"] = 45.0
    # 最不利测点失效：BT-1201-G3 的数据全部去掉，另外两个测点正常——不能因此判满足（fail closed）
    one_tube_down = copy.deepcopy(ready)
    tg = one_tube_down["groups"]["tubes"]
    tg["latest"] = [r for r in tg["latest"] if r["sensor_id"] != "BT-1201-G3"]
    tg["readings"] = [r for r in tg["readings"] if r["sensor_id"] != "BT-1201-G3"]
    # 最新读数过旧：BT-1201-G3 只留 4 小时前及更早的读数
    stale_tube = copy.deepcopy(ready)
    cut = (datetime.fromisoformat(ready["as_of"]) - timedelta(hours=4)).isoformat()
    sg = stale_tube["groups"]["tubes"]
    sg["latest"] = [r for r in sg["latest"] if r["sensor_id"] != "BT-1201-G3"]
    sg["readings"] = [r for r in sg["readings"] if r["sensor_id"] != "BT-1201-G3" or r["ts"] <= cut]
    return {
        (UP, "unsealing_plan", "s5-336h-one-tube-down"): {"evidence": one_tube_down},
        (UP, "unsealing_plan", "s5-336h-stale-tube"): {"evidence": stale_tube},
        (UP, "unsealing_plan", "s5-336h-ready"): {"evidence": ready},
        (UP, "unsealing_plan", "s5-240h-sealed-too-short"): {"evidence": early},
        (UP, "unsealing_plan", "s5-336h-no-tube-data"): {"evidence": no_tubes},
        (UP, "unsealing_plan", "s5-336h-temperature-high"): {"evidence": hot},
    }


def unseal_template_inputs(inputs: dict) -> dict[tuple[str, str, str], dict]:
    out = {}
    for case in ("s5-336h-ready", "s5-240h-sealed-too-short"):
        data = inputs[(UP, "unsealing_plan", case)]
        rec = fake_record("unseal", run(UP, "unsealing_plan", data), 0)
        rec["as_of"] = data["evidence"]["as_of"]
        for tpl in ("report", "brief"):
            out[(UP, tpl, case)] = report_data({"unseal": rec})
    return out


def incident_template_inputs(inputs: dict) -> dict[tuple[str, str, str], dict]:
    out = {}
    for case in ("full-process-closed", "in-progress-unfinished", "with-approvals-and-receipts"):
        data = inputs[(IR, "timeline", case)]
        rec = fake_record("timeline", run(IR, "timeline", data), 0)
        rec["panel"], rec["as_of"] = "1201", data["evidence"]["as_of"]
        for tpl in ("report", "brief"):
            out[(IR, tpl, case)] = report_data({"timeline": rec})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", action="store_true", help="只按现有输入重写期望")
    args = ap.parse_args()
    if args.expected:
        for skill in (FA, XT, ESC, SP, PC, ER, IR, UP, FP):
            for inp in sorted((SKILLS / skill / "tests" / "cases").glob("*/*.input.json")):
                write_expected(skill, inp.parent.name, inp.name.removesuffix(".input.json"), json.loads(inp.read_text(encoding="utf-8")))
                print(f"期望  {skill}/{inp.parent.name}/{inp.name}")
            for inp in sorted((SKILLS / skill / "tests" / "templates").glob("*/*.input.json")):
                write_template_expected(skill, inp.parent.name, inp.name.removesuffix(".input.json"), json.loads(inp.read_text(encoding="utf-8")))
                print(f"期望  {skill}/templates/{inp.parent.name}/{inp.name}")
        return 0
    inputs = {**build_inputs(), **sealing_inputs(), **effect_inputs(), **incident_inputs(), **unseal_inputs(), **firefighting_inputs()}
    for (skill, template, case), data in {**build_template_inputs(inputs), **sealing_template_inputs(inputs), **effect_template_inputs(inputs), **incident_template_inputs(inputs), **unseal_template_inputs(inputs), **firefighting_template_inputs(inputs)}.items():
        write_template_case(skill, template, case, data)
        print(f"模板  {skill}/{template}/{case}")
    for (skill, script, case), data in inputs.items():
        d = case_dir(skill, script)
        d.mkdir(parents=True, exist_ok=True)
        # 输入不缩进（证据较大），期望缩进便于审阅差异
        (d / f"{case}.input.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        write_expected(skill, script, case, data)
        print(f"样例  {skill}/{script}/{case}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
