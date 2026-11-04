#!/usr/bin/env python3
"""生成第二版火灾闭环技能（fire-assessment、explosion-triangle、emergency-escalation、sealing-plan、plan-compliance、effect-review）的自检样例。

输入取自模拟矿井的标准场景（手动时钟，Python 采集小工具按 fire-assessment 的采集清单取证据），
期望输出是当前脚本的运行结果（金标准；关键字段与第一版服务的一致性由 mcp/workflow/tests/test_skill_parity.py 保证）。

    uv run --project mcp python scripts/make_skill_cases.py            # 重新生成全部样例的输入和期望
    uv run --project mcp python scripts/make_skill_cases.py --expected # 只按现有输入重写期望（改了参数或算法后）

改参数后重写期望，提交说明里写明是占位值还是专家审定值（skills/README.md）。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path

from rescue_contract.collect import collect_from, parse_collect_spec
from rescue_contract.skills import load_skill, run_script
from rescue_contract.template import render, report_data
from rescue_mine_data_sim.server import SimHolder, build_server

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
FA, XT, ESC = "fire-assessment", "explosion-triangle", "emergency-escalation"
SP, PC, ER = "sealing-plan", "plan-compliance", "effect-review"


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
    intense = copy.deepcopy(w["s4-60"])
    intense["result"]["values"]["c2h2_ppm"] = 0.2
    return {
        (FA, "warning", "s4-escalation-60h-red"): {"evidence": s4_60},
        (FA, "warning", "s6-sensor-faults-36h"): {"evidence": s6_36},
        (FA, "stage", "s4-escalation-60h-accelerating"): {"warning": w["s4-60"]},
        (FA, "stage", "s1-normal-24h-no-sign"): {"warning": w["s1-24"]},
        (FA, "stage", "intense-c2h2"): {"warning": intense},
        (FA, "alarms", "s5-unsealed-312h-reignition"): {"evidence": s5_312},
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
    return out


def sealing_template_inputs(inputs: dict) -> dict[tuple[str, str, str], dict]:
    out = {}
    for case in ("s4-escalation-60h-full", "s4-escalation-60h-minimal"):
        plan = run(SP, "compose", inputs[(SP, "compose", case)])
        refs = plan["result"]["plan"]["basis"]
        out[(SP, "plan", case)] = report_data({"compose": fake_record("compose", plan, 0)}, {"articles": index_articles([{"document": b["document"], "article": b["article"]} for b in refs])})
    for case in ("pass", "redline-fail-needs-human"):
        data = inputs[(PC, "review", case)]
        rev = run(PC, "review", data)
        out[(PC, "review", case)] = report_data({"review": fake_record("review", rev, 0)}, {"articles": index_articles(data["scope"]["result"]["articles"])})
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", action="store_true", help="只按现有输入重写期望")
    args = ap.parse_args()
    if args.expected:
        for skill in (FA, XT, ESC, SP, PC, ER):
            for inp in sorted((SKILLS / skill / "tests" / "cases").glob("*/*.input.json")):
                write_expected(skill, inp.parent.name, inp.name.removesuffix(".input.json"), json.loads(inp.read_text(encoding="utf-8")))
                print(f"期望  {skill}/{inp.parent.name}/{inp.name}")
            for inp in sorted((SKILLS / skill / "tests" / "templates").glob("*/*.input.json")):
                write_template_expected(skill, inp.parent.name, inp.name.removesuffix(".input.json"), json.loads(inp.read_text(encoding="utf-8")))
                print(f"期望  {skill}/templates/{inp.parent.name}/{inp.name}")
        return 0
    inputs = {**build_inputs(), **sealing_inputs(), **effect_inputs()}
    for (skill, template, case), data in {**build_template_inputs(inputs), **sealing_template_inputs(inputs), **effect_template_inputs(inputs)}.items():
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
