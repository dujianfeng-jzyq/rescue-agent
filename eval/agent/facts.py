"""固定流程结果里的“关键事实”，以及最后回复是否如实传达了它们（智能体评测的 B 类检查）。

固定流程（run_fire_assessment、review_effect、draft_sealing_plan、assess_unsealing、review_timeline）的工具结果里有结构化的
`result` 和 `record_ids`：预警等级、阶段、分区、步骤数、满足项数、记录编号……这些是代码算出来的事实。检查对象是这些值有没有
出现在回复里、有没有被相反的说法顶替，不比较要点的措辞：智能体把“预警等级无”写成“预警等级为无”不影响任何事实。

自由文字（阶段子 Agent 写的研判说明、合规审核意见）不在这里：它们没有结构化来源，由评测员检查（见 eval/agent/README.md）。

每个事实 Fact：
- require：这些写法至少出现一个（正则；字符串按字面匹配）；
- forbid：这些写法一个都不能出现（用于“建议升级”与“不建议升级”这类互相包含的相反说法）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Fact:
    name: str
    require: list[str]
    forbid: list[str] = field(default_factory=list)


def _lit(s: str) -> str:
    return re.escape(_norm(str(s)))


def _num(n: object) -> str:
    """独立的数（前后不是数字，不是小数的一部分）。"""
    return rf"(?<![\d.]){re.escape(str(n))}(?![\d]|\.\d)"


def _count(label: str, n: object) -> str:
    """“已受控 2 个”“研判 18 次”：标签后面隔几个非数字字符就是这个数。"""
    return rf"{re.escape(label)}[^\d\n]{{0,6}}{_num(n)}"


_DATE_T = re.compile(r"(\d)T(\d)")
_SECONDS = re.compile(r"(\d{2}:\d{2}):00(?!\d)")


def _norm(text: str) -> str:
    """去掉加粗和空白；日期时间的 T 换成空格、去掉整零秒（报告里 2026-11-04T08:00:00 与 2026-11-04 08:00 是同一个时刻）。"""
    text = text.replace("**", "")
    text = _DATE_T.sub(r"\1 \2", text)
    text = _SECONDS.sub(r"\1", text)
    return re.sub(r"[ \t\u3000]+", "", text)  # 换行保留：数字不会跨行粘在一起


def _labeled(name: str, label: str, value: object) -> Fact | None:
    """值要紧跟在标签后面（隔不超过 8 个非换行字符）。“无”“需继续处置”这类短词在回复别处也会碰巧出现，不绑标签等于没查。"""
    return Fact(name, [rf"{label}[^\n]{{0,8}}{_lit(value)}"]) if value not in (None, "") else None


def _present(name: str, value: object) -> Fact | None:
    return Fact(name, [_lit(value)]) if value not in (None, "") else None


def _fire_assessment(res: dict, ids: dict) -> list[Fact | None]:
    w, ex, pe, es = res.get("warning") or {}, res.get("explosion") or {}, res.get("personnel") or {}, res.get("escalation") or {}
    worst = next((p for p in ex.get("points", []) if p.get("sensor_id") == ex.get("worst_sensor_id")), None)
    out: list[Fact | None] = [
        _labeled("预警等级", "预警等级", w.get("level_name")),
        _labeled("自燃发展阶段", "阶段", w.get("stage_name")),
        _present("爆炸危险性", ex.get("overall_name")),
        _present("记录编号（预警等级）", ids.get("warning")),
        _present("记录编号（升级建议）", ids.get("escalation")),
    ]
    if worst:
        out += [_present("最不利测点", worst.get("sensor_id")), _present("最不利测点分区", worst.get("zone_name"))]
    alarms = res.get("alarms") or []
    if alarms:
        out += [_present(f"实时报警 {a.get('sensor_id')}", a.get("sensor_id")) for a in alarms]
    else:
        out.append(Fact("实时报警：无", [r"报警[：:]?无", r"无[^，。；\n]{0,4}报警", r"没有[^，。；\n]{0,4}报警"]))
    if pe.get("computed"):
        out.append(Fact("受威胁人数", [_num(pe["total_threatened"]) + r"人", _count("威胁", pe["total_threatened"])]))
    elif pe.get("note"):
        out.append(Fact("受威胁人员：未计算", [r"未计算", _lit(pe["note"])]))
    # “建议升级”被“不建议升级”包含：写成相反说法的互斥
    if es.get("recommend"):
        out.append(Fact("升级建议：建议升级", [_lit("建议升级")], forbid=[_lit("不建议升级")]))
    elif "recommend" in es:
        out.append(Fact("升级建议：暂不建议升级", [_lit("不建议升级")]))
    out += [_present(f"条文依据 {a.get('article')}", a.get("article")) for a in es.get("articles", [])]
    if res.get("chart_markdown"):
        out.append(Fact("风险图", [_lit(res["chart_markdown"])]))
    return out


def _effect(res: dict, ids: dict) -> list[Fact | None]:
    out: list[Fact | None] = [
        _labeled("结论", "结论", res.get("overall_name")),
        _present("处置开始", res.get("since")),
        _present("已观测小时", res.get("observed_h")) and Fact("已观测小时", [_num(res["observed_h"]) + r"\s*小时"]),
        _present("下次复评", res.get("next_review_at")),
        _present("记录编号（效果复评）", ids.get("effect")),
    ]
    # 标签里带逗号的（“观测时间不足，暂不下结论”）报告里会缩短，取逗号前
    out += [Fact(f"{label} {n}", [_count(re.split(r"[，,]", label)[0], n)]) for label, n in (res.get("counts") or {}).items()]
    if res.get("escalation_suggested"):
        out.append(Fact("结论为恶化：建议重新研判", [r"重新研判"]))
    return out


def _sealing_plan(res: dict, ids: dict) -> list[Fact | None]:
    out: list[Fact | None] = [
        _present("方案编号", res.get("plan_id")),
        _present("合规审核结论", res.get("verdict_name")),
        Fact("步骤数", [_num(res["step_count"]) + r"\s*步"]) if "step_count" in res else None,
        _present("记录编号（方案）", ids.get("compose")),
        _present("记录编号（审核）", res.get("issue_record_id") or ids.get("review")),
    ]
    if res.get("shortage_count"):
        out.append(Fact("物资缺口项数", [_num(res["shortage_count"]) + r"\s*项"]))
    elif "shortage_count" in res:
        out.append(Fact("物资无缺口", [r"无缺口", r"没有缺口"]))
    if res.get("redline_ok") is False:
        out.append(Fact("红线预检未通过", [r"未通过", r"不通过"]))
    return out


def _unsealing(res: dict, ids: dict) -> list[Fact | None]:
    ready = res.get("ready")
    out: list[Fact | None] = [
        Fact("启封结论", [_lit("具备启封条件")], forbid=[_lit("不具备")]) if ready else Fact("启封结论", [_lit("不具备启封条件")]),
        Fact("判定总数", [_count("共", res["total_count"])]) if "total_count" in res else None,
        Fact("满足项数", [_num(res["met_count"]) + r"\s*项满足"]) if "met_count" in res else None,
        Fact("不满足项数", [_num(res["unmet_count"]) + r"\s*项不满足"]) if "unmet_count" in res else None,
        _present("记录编号（启封评估）", ids.get("unseal")),
    ]
    if not ready:
        out += [_present(f"不满足的项 {x}", x) for x in re.split(r"[、,，;；]\s*", res.get("unmet_labels") or "") if x]
    return out


def _timeline(res: dict, ids: dict) -> list[Fact | None]:
    out: list[Fact | None] = [_present("事件编号", res.get("incident_id")), _present("事件状态", res.get("status_name"))]
    out += [Fact(f"{label} {n}", [_count(label, n)]) for label, n in re.findall(r"([一-鿿]{2,8})\s*(\d+)", res.get("counts_text") or "")]
    return out


FLOWS = {
    "run_fire_assessment": _fire_assessment,
    "review_effect": _effect,
    "draft_sealing_plan": _sealing_plan,
    "assess_unsealing": _unsealing,
    "review_timeline": _timeline,
}


def facts_for(tool: str, payload: dict) -> list[Fact]:
    """某次固定流程调用的关键事实。payload 是工具结果的 JSON（含 result、record_ids）；没有 result（出错）返回空。"""
    fn = FLOWS.get(tool)
    res = payload.get("result")
    if not fn or not isinstance(res, dict):
        return []
    return [f for f in fn(res, payload.get("record_ids") or {}) if f]


def check_reply(facts: list[Fact], reply: str) -> dict:
    """{"missing": [...], "contradicted": [...], "total": n}：缺失的事实名和被相反说法顶替的事实名。"""
    text = _norm(reply)
    missing, contradicted = [], []
    for f in facts:
        if not any(re.search(p, text) for p in f.require):
            missing.append(f.name)
        elif any(re.search(p, text) for p in f.forbid):
            contradicted.append(f.name)
    return {"missing": missing, "contradicted": contradicted, "total": len(facts)}
