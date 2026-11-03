"""第二版 P1 验收：技能脚本与第一版服务对 6 个标准场景的关键时刻输出一致（docs/design-v2.md 第 9 节）。

同一时刻的模拟矿井：
- 第一版：workflow 的 assess_panel（fire_model 的预警、阶段、报警、趋势、爆炸三角形，ventnet 的受威胁人员）+ recommend_escalation；
- 第二版：Python 采集小工具按 fire-assessment 的采集清单（assessment）取证据，运行 fire-assessment、explosion-triangle、
  emergency-escalation 的脚本（与证据插件同一套运行规则）。

比较等级、阶段、指标、触发条件、报警、趋势、爆炸分区（逐测点）、受威胁人员、升级建议。数据质量说明的顺序可能不同
（第二版的测点按 id 排序），按集合比较。第二版 check.py 的等级、阶段、报警也要与完整研判一致。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from rescue_contract.client import MineData
from rescue_contract.collect import collect_from, parse_collect_spec
from rescue_contract.skills import load_skill, run_script
from rescue_fire_model import explosion
from rescue_mine_data_sim.server import SimHolder, build_server
from rescue_workflow import assess, escalation

SKILLS = Path(__file__).resolve().parents[3] / "skills"

# （场景，矿井小时，处置）：各场景的关键时刻取自 eval/expectations.yaml（到达各等级的期限、启封前后）
MOMENTS = [
    ("s1-normal", 24, None),
    ("s1-normal", 72, None),
    ("s2-slow-oxidation", 66, None),
    ("s2-slow-oxidation", 90, None),
    ("s2-slow-oxidation", 120, None),
    ("s3-accelerating", 33, None),
    ("s3-accelerating", 39, None),
    ("s3-accelerating", 63, None),
    ("s4-escalation", 45, None),
    ("s4-escalation", 60, None),
    ("s4-escalation", 72, None),
    ("s5-sealed-fire-area", 120, None),
    ("s5-sealed-fire-area", 264, 240),  # 第 240 小时启封
    ("s5-sealed-fire-area", 312, 240),  # 启封后复燃报警（第164条）
    ("s6-sensor-faults", 36, None),
    ("s6-sensor-faults", 72, None),
]


def _run(skill, script, data):
    r = run_script(skill.scripts[script], data)
    assert r.ok, f"{skill.name}/{script}：{r.status} {r.detail}"
    return r.output


def _v2(holder, panel="1201"):
    fa, xt, esc = (load_skill(SKILLS / n) for n in ("fire-assessment", "explosion-triangle", "emergency-escalation"))
    spec = parse_collect_spec((SKILLS / "fire-assessment" / "references" / "collect.yaml").read_text(encoding="utf-8"))
    ev = asyncio.run(collect_from(build_server(holder), spec, "assessment", panel))
    out = {"evidence": ev}
    out["alarms"] = _run(fa, "alarms", {"evidence": ev})
    out["warning"] = _run(fa, "warning", {"evidence": ev})
    out["stage"] = _run(fa, "stage", {"warning": out["warning"]})
    out["trend"] = _run(fa, "trend", {"evidence": ev, "warning": out["warning"]})
    out["personnel"] = _run(fa, "personnel", {"evidence": ev, "warning": out["warning"], "options": {"include_personnel": True}})
    out["explosion"] = _run(xt, "explosion_triangle", {"evidence": ev})
    out["escalation"] = _run(esc, "escalation", {k: out[k] for k in ("warning", "stage", "alarms")})
    out["check"] = _run(fa, "check", {"evidence": ev})
    return out


def _v1(holder, panel="1201"):
    md = MineData(build_server(holder))
    r = asyncio.run(assess.assess_panel(md, panel, explosion.load_constants(), "http://test", include_personnel=True))
    return r, escalation.recommend(r, None)


def _key(d):
    return sorted(d, key=lambda x: str(sorted(x.items())))


@pytest.mark.parametrize(("scenario", "hours", "unseal_at"), MOMENTS, ids=[f"{s}@{h}h" for s, h, _ in MOMENTS])
def test_skills_match_v1_services(scenario, hours, unseal_at):
    holder = SimHolder(scenario, manual_clock=True)
    if unseal_at is not None:
        holder.engine.clock.advance(unseal_at)
        holder.engine.apply("unseal", None, source="eval")
        holder.engine.clock.advance(hours - unseal_at)
    else:
        holder.engine.clock.advance(hours)
    old, old_esc = _v1(holder)
    new = _v2(holder)
    w_old, w_new = old["warning"], new["warning"]["result"]

    assert new["evidence"]["as_of"] == old["as_of"][:19]
    # 预警等级与指标
    assert w_new["level"] == w_old["level"]
    assert w_new["level_name"] == w_old["level_name"]
    assert w_new["triggered"] == w_old["triggered"]
    assert w_new["indicators"] == w_old["indicators"]
    assert w_new["indicators"] == {x["label"]: x["value"] for x in w_new["indicator_list"]}
    assert {k: v for k, v in w_new["sources"].items()} == {
        k: v for k, v in zip(_keys_of(w_old["indicator_sources"]), w_old["indicator_sources"].values(), strict=True)
    }
    assert sorted(w_new["data_quality"]) == sorted(w_old["data_quality"])
    assert sorted(w_new["notes"]) == sorted(w_old["notes"])
    # 自燃阶段
    s_new = new["stage"]["result"]
    assert (s_new["stage"], s_new["stage_name"], s_new["stage_basis"]) == (w_old["stage"], w_old["stage_name"], w_old["stage_basis"])
    # 实时报警
    assert _key(new["alarms"]["result"]["alarms"]) == _key(old["realtime_alarms"])
    # 趋势
    t_old = sorted(old["trends"], key=lambda t: (t["sensor_id"], t["measurand"]))
    t_new = sorted(new["trend"]["result"]["trends"], key=lambda t: (t["sensor_id"], t["measurand"]))
    assert t_new == t_old
    # 爆炸三角形（逐测点）
    x_old, x_new = old["explosion"], new["explosion"]["result"]
    assert (x_new["overall"], x_new["overall_name"], x_new["worst_sensor_id"]) == (x_old["overall"], x_old["overall_name"], x_old["worst_sensor_id"])
    assert x_new["failures"] == x_old["failures"]
    fields = ("sensor_id", "zone", "zone_name", "explosion_risk", "meaning", "warnings", "sample", "data_quality")
    assert [{k: p[k] for k in fields} for p in x_new["points"]] == [{k: p[k] for k in fields} for p in x_old["points"]]
    # 受威胁人员
    p_new = {k: v for k, v in new["personnel"]["result"].items() if k not in ("panel", "as_of", "computed", "note")}
    for g in p_new["threatened"]:
        assert g.pop("persons_text") == "、".join(g["persons"])
    assert p_new == old["personnel"]
    # 升级建议
    e_new = new["escalation"]["result"]
    for k in ("recommend", "reasons", "not_met", "evacuation_reminder", "level", "level_name", "stage_name"):
        assert e_new[k] == old_esc[k], k
    assert [(a["document"], a["article"], a["content"]) for a in e_new["articles"]] == [
        (a["document"], a["article"], a["content"]) for a in old_esc["articles"]
    ]
    # 风险检测与完整研判一致
    c = new["check"]["result"]
    assert (c["level"], c["stage"]) == (w_old["level"], w_old["stage"])
    assert _key(c["alarms"]) == _key(old["realtime_alarms"])


def _keys_of(labelled):
    from rescue_fire_model.warning import LABELS

    back = {v: k for k, v in LABELS.items()}
    return [back[k] for k in labelled]
