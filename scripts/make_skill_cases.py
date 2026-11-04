#!/usr/bin/env python3
"""生成第二版火灾技能（fire-assessment、explosion-triangle、emergency-escalation）的自检样例。

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


def evidence(scenario: str, hours: float, kind: str = "assessment", unseal_at: float | None = None) -> dict:
    holder = SimHolder(scenario, manual_clock=True)
    if unseal_at is not None:
        holder.engine.clock.advance(unseal_at)
        holder.engine.apply("unseal", None, source="eval")
        hours -= unseal_at
    holder.engine.clock.advance(hours)
    spec = parse_collect_spec((SKILLS / FA / "references" / "collect.yaml").read_text(encoding="utf-8"))
    return asyncio.run(collect_from(build_server(holder), spec, kind, "1201"))


def run(skill: str, script: str, data: dict) -> dict:
    r = run_script(load_skill(SKILLS / skill).scripts[script], data)
    if not r.ok:
        raise SystemExit(f"{skill}/{script}：{r.status} {r.detail}")
    return r.output


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
        for skill in (FA, XT, ESC):
            for inp in sorted((SKILLS / skill / "tests" / "cases").glob("*/*.input.json")):
                write_expected(skill, inp.parent.name, inp.name.removesuffix(".input.json"), json.loads(inp.read_text(encoding="utf-8")))
                print(f"期望  {skill}/{inp.parent.name}/{inp.name}")
            for inp in sorted((SKILLS / skill / "tests" / "templates").glob("*/*.input.json")):
                write_template_expected(skill, inp.parent.name, inp.name.removesuffix(".input.json"), json.loads(inp.read_text(encoding="utf-8")))
                print(f"期望  {skill}/templates/{inp.parent.name}/{inp.name}")
        return 0
    inputs = build_inputs()
    for (skill, template, case), data in build_template_inputs(inputs).items():
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
