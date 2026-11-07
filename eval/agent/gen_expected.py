#!/usr/bin/env python3
"""生成用例的期望决策 B 的草稿（不经过智能体）：按用例的场景和时刻，用技能脚本直接算研判、升级建议、复评，再按下面的“占位策略”
写出 expect_decisions。草稿由人审阅后提交进 cases.yaml；阈值或场景改了，重新生成，diff 就是期望的变化。

    uv run --project mcp python eval/agent/gen_expected.py                 # 打印三个用例的草稿
    uv run --project mcp python eval/agent/gen_expected.py --write         # 写进 cases.yaml（替换已有的 expect_decisions 块）

占位策略（我们的猜测，不是专家规则，待审定）：
- 建议升级 → 必须提交 escalate_emergency（不约束 recommended_level：契约里它是自由字符串）；不建议升级 → 不提交；
- 预警等级红 → 必须提交 publish_warning（level=red），撤人通知 notify_evacuation 可选；橙、黄、蓝 → 预警可提交可不提交（门槛没有定义）；无 → 不提交；
- 复评结论“需继续处置/恶化” → 必须登记新的跟踪计划、记录复评结论，且不得关闭事件；“已受控” → 记录复评结论，不得关闭事件。
  唤醒驱动的运行都不得关闭事件（技能：操作员确认可以结束时才 close_incident）。
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).resolve().parent / "cases.yaml"
FA, ESC, ER = "fire-assessment", "emergency-escalation", "effect-review"
CASE_IDS = ["v3-wake-anomaly", "s4-watch-wake-assess", "v3-followup-due"]


def _msc():
    spec = importlib.util.spec_from_file_location("make_skill_cases", ROOT / "scripts" / "make_skill_cases.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_skill_cases"] = mod
    spec.loader.exec_module(mod)
    return mod


def assessment(msc, scenario: str, hours: float) -> dict:
    ev = msc.evidence(scenario, hours)
    w = msc.run(FA, "warning", {"evidence": ev})
    st = msc.run(FA, "stage", {"warning": w})
    al = msc.run(FA, "alarms", {"evidence": ev})
    esc = msc.run(ESC, "escalation", {"warning": w, "stage": st, "alarms": al})
    return {"level": w["result"]["level"], "stage": st["result"]["stage"], "recommend": esc["result"]["recommend"], "alarms": len(al["result"]["alarms"])}


def effect(msc, case: dict) -> dict:
    acts = [(a["type"], a.get("params"), a.get("roadways")) for a in case["apply"]]
    at = case["apply"][0]["at_h"]
    ev = msc.effect_evidence(case["scenario"], at, acts, case["at_h"])
    r = msc.run(ER, "effect", {"evidence": ev})["result"]
    return {"overall": r["overall"], "overall_name": r["overall_name"]}


def expected(msc, case: dict) -> dict:
    if case["id"] == "v3-followup-due":
        eff = effect(msc, case)
        ops = {"required": ["complete_followup"] + (["add_followup"] if eff["overall"] in ("continuing", "worsening") else []), "forbidden": ["close_incident"]}
        return {"status": "placeholder", "derived_from": {"scenario": case["scenario"], "effect": eff["overall"]}, "flows_called": ["review_effect"], "incident_ops": ops}
    hours = case["at_h"] + case.get("advance_h", 0)
    a = assessment(msc, case["scenario"], hours)
    required, optional = {}, {}
    if a["recommend"]:
        # recommended_level 是自由字符串（契约没有定义取值），智能体有时填 red、有时填“红色（危险）”，不约束
        required["escalate_emergency"] = {}
    if a["level"] == "red":
        required["publish_warning"] = {"level": "red"}
        optional["notify_evacuation"] = {}  # 红色且有受威胁人员时主动提交撤人通知看起来合理；是否必须待专家定
    elif a["level"] != "none":
        optional["publish_warning"] = {"level": a["level"]}
    flows = ["run_fire_assessment"] + (["check_mine"] if case["id"] == "s4-watch-wake-assess" else [])
    return {
        "status": "placeholder",
        "derived_from": {"scenario": case["scenario"], "at_h": hours, "level": a["level"], "stage": a["stage"], "recommend_escalation": a["recommend"], "realtime_alarms": a["alarms"]},
        "external_actions": {"required": required, "optional": optional},
        "flows_called": flows,
        "incident_ops": {"forbidden": ["close_incident"]},
    }


def render(block: dict) -> str:
    text = yaml.safe_dump({"expect_decisions": block}, allow_unicode=True, sort_keys=False, default_flow_style=None, width=200)
    return "".join("    " + line + "\n" for line in text.splitlines())


def write(case_id: str, block_text: str) -> None:
    lines = CASES.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"- id: {case_id}"))
    chk = next(i for i in range(start, len(lines)) if lines[i].startswith("  checks:"))
    # 删掉已有的 expect_decisions 块（缩进 4 的下一行起，缩进大于 4 的都属于它）
    j = chk + 1
    while j < len(lines):
        if lines[j].startswith("    expect_decisions:"):
            k = j + 1
            while k < len(lines) and (lines[k].startswith("      ") or not lines[k].strip()) and lines[k].strip():
                k += 1
            del lines[j:k]
            break
        if not lines[j].startswith("    "):
            break
        j += 1
    lines[chk + 1 : chk + 1] = [block_text]
    CASES.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    cases = {c["id"]: c for c in yaml.safe_load(CASES.read_text(encoding="utf-8"))}
    msc = _msc()
    for cid in CASE_IDS:
        text = render(expected(msc, cases[cid]))
        print(f"# {cid}\n{text}")
        if a.write:
            write(cid, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
