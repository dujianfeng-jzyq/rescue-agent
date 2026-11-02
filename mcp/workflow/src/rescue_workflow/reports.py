"""报告事实部分的模板（skills/shared/report-templates 的代码实现，二者有同步测试）。

只写结果里已有的值：不取整、不加“约”、不合并区间、不推断。缺失就写缺失。
"""

from __future__ import annotations

from typing import Any


def _v(x: Any) -> str:
    return "缺失" if x is None else str(x)


def _ts(iso: str | None) -> str:
    return "缺失" if not iso else iso.replace("T", " ")


def assessment(r: dict[str, Any], snapshot_id: str) -> str:
    w = r["warning"]
    lines = [
        f"【预警研判】{r['panel']} 工作面 {_ts(r['as_of'])}",
        f"结论：预警等级 {w['level_name']}（计算结果）",
        f"自燃发展阶段：{w['stage_name']}（计算结果）" + (f"；依据：{'；'.join(w['stage_basis'])}" if w["stage_basis"] else ""),
    ]
    alarms = r["realtime_alarms"]
    if alarms:
        lines.append("实时报警：")
        lines += [f"- [{a['article']}] {a['sensor_id']}：{a['message']}" for a in alarms]
    else:
        lines.append("实时报警：无")
    lines.append("触发条件：" + ("" if w["triggered"] else "无"))
    lines += [f"- {t}" for t in w["triggered"]]
    lines.append("关键指标（计算结果）：")
    src = w.get("indicator_sources", {})
    for name, val in w["indicators"].items():
        where = f"（{src[name]['sensor_id']}）" if name in src else ""
        lines.append(f"- {name}：{_v(val)}{where}")
    if r["trends"]:
        lines.append("趋势（计算结果）：")
        for t in r["trends"]:
            lines.append(
                f"- {t['sensor_id']} {t['measurand']} 近 {t['hours']} 小时：{_v(t['first'])}（{_ts(t['first_ts'])}）→ "
                f"{_v(t['latest'])}（{_ts(t['latest_ts'])}），变化 {_v(t['change'])}，速率 {_v(t['rate_per_hour'])}/h"
            )
    x = r["explosion"]
    lines.append(f"爆炸危险性（多组分爆炸三角形，计算结果）：{x['overall_name']}")
    for p in x["points"]:
        lines.append(f"- {p['sensor_id']}：{p['zone']} 区 {p['zone_name']}。{p['meaning']}")
    for f in x["failures"]:
        lines.append(f"- {f['sensor_id']}：未能计算。{f['error']}")
    if r["personnel"] is not None:
        pr = r["personnel"]
        lines.append(f"受威胁人员（通风网络估算，未做风量解算）：受威胁 {pr['total_threatened']} 人，井下共 {pr['total_underground']} 人")
        for g in pr["threatened"]:
            route = g["escape_route"]
            way = (
                f"避灾路线至 {route['target_name']}，{route['length_m']} 米，佩戴自救器步行 {route['walk_with_self_rescuer_min']} 分钟"
                if route.get("reachable") else route.get("note", "无可达出口，需人工研判")
            )  # fmt: skip
            lines.append(f"- {g['roadway_id']}：{g['count']} 人（{'、'.join(g['persons'])}），有害气体到达 {g['arrival_min']} 分钟；{way}")
        lines.append("撤人按预案立即执行，不等待本结果；以上名单是补充核对用的。")
    else:
        lines.append(f"受威胁人员：{r['personnel_note']}")
    dq = w["data_quality"] + [q for t in r["trends"] for q in t["data_quality"]]
    lines.append("数据质量：" + ("无异常" if not dq else ""))
    lines += [f"- {q}" for q in dict.fromkeys(dq)]
    if w["notes"]:
        lines.append("说明：")
        lines += [f"- {n}" for n in w["notes"]]
    lines.append(f"依据：研判快照 {snapshot_id}（workflow 计算结果）；阈值和判据为开发占位值")
    out = "\n".join(lines)
    if x.get("chart_markdown"):
        out += "\n\n" + x["chart_markdown"]
    return out


STEP_NAMES = {
    "monitor": "检测观测", "evacuate": "撤人", "power_cutoff": "切断电源", "inert_injection": "注惰",
    "grouting": "注浆", "inhibitor_spray": "喷洒阻化剂", "leak_sealing": "堵漏", "pressure_balancing": "均压",
    "temporary_seal": "临时密闭", "permanent_seal": "永久密闭", "unseal": "启封",
}  # fmt: skip
RISK_NAMES = {
    "explosive": "处于爆炸范围",
    "may_become_explosive": "可能进入爆炸范围",
    "not_explosive": "当前不在爆炸范围",
    "unknown": "无法判定（按有爆炸危险对待）",
}


def _yn(v: Any) -> str:
    return "缺失" if v is None else ("是" if v else "否")


def plan_draft(r: dict[str, Any], snapshot_id: str) -> str:
    p, c = r["plan"], r["plan"]["context"]
    human = {h["field"]: h for h in r["human_inputs"]}

    def manual(field: str, label: str, fmt=_v) -> str:
        if field in human:
            h = human[field]
            return f"- {label}：{fmt(h['value'])}（人工确认：{h['confirmed_by']}）"
        return f"- {label}：缺失（需现场确认）"

    lines = [
        f"【方案草案】{p['plan_id']} {p['title']}",
        "现场条件（代码生成，不能修改）：",
        f"- 甲烷浓度（%）：{_v(c['methane_pct'])}；是否继续上升：{_yn(c['methane_rising'])}",
        f"- 火区氧浓度（%）：{_v(c['o2_pct'])}",
        f"- 爆炸危险性：{RISK_NAMES[c['explosion_risk']]}",
        f"- 火区封闭时间：{_ts(c['sealed_at'])}",
        manual("area_temperature_c", "灾区温度（℃）"),
        manual("visibility_m", "能见度（米）"),
        manual("fire_source_confirmed", "火源是否明确", _yn),
        manual("water_supply_sufficient", "水源、人力和物力是否充足", _yn),
        manual("return_airway_clear", "回风道是否畅通", _yn),
        "必备步骤（不能删除或调换顺序，可在中间增加步骤）：",
    ]
    for s in p["steps"]:
        extra = []
        if s["roadways"]:
            extra.append("巷道 " + "、".join(s["roadways"]))
        if "rate_m3_h" in s["params"]:
            extra.append(f"注氮流量 {s['params']['rate_m3_h']} m³/h（估算）")
        lines.append(
            f"{s['order']}. {STEP_NAMES.get(s['type'], s['type'])}：{s['description']}" + (f"（{'；'.join(extra)}）" if extra else "")
        )
    if r["missing_inputs"]:
        blocking = r.get("blocking_inputs")
        if blocking:
            lines.append("待补充（红线自查因缺少现场条件不通过，须先由人确认）：")
        else:
            lines.append("待补充（不影响红线自查结论，可以继续提交，人工确认时由确认人把关）：")
        lines += [f"- {m}" for m in r["missing_inputs"]]
    chk = r.get("redline_check")
    if chk is not None:
        lines.append("红线自查：" + ("通过" if chk["ok"] else "未通过"))
        lines += [f"- {x}" for x in chk["problems"]]
    lines.append("依据条款：" + "、".join(f"{b['document']} {b['article']}" for b in p["basis"]))
    lines.append(f"依据：方案草案快照 {snapshot_id}（workflow 计算结果）；判据为开发占位值")
    return "\n".join(lines)


def effect_review(r: dict[str, Any], snapshot_id: str) -> str:
    lines = [
        f"【效果评估】{r['panel']} 工作面 {_ts(r['as_of'])}",
        f"结论：{r['overall_name']}（计算结果）",
        f"处置开始：{_ts(r['since'])}（{r['since_source']}）；已观测 {r['observed_h']} 小时",
        "依据：" + "；".join(r["basis"]),
        "各结论测点数：" + "，".join(f"{k} {v}" for k, v in r["counts"].items()),
        *(
            [
                f"- {g['type']} {g['measurand']} 共 {g['total']} 个：" + "，".join(f"{k} {v}" for k, v in g["counts"].items())
                for g in r["counts_by_type"]
            ]
            if r.get("counts_by_type")
            else []
        ),
        "逐点结果（计算结果）：",
    ]
    for p in r["points"]:
        markers = f"；新出现 {'、'.join(p['new_marker_gases'])}" if p["new_marker_gases"] else ""
        lines.append(
            f"- {p['sensor_id']} {p['measurand']}：{p['effect_name']}。处置时 {_v(p['value_at_since'])}，现在 {_v(p['latest'])}，"
            f"变化 {_v(p['change_since'])}；速率 处置前 {_v(p['rate_before_per_hour'])}/h、处置后 {_v(p['rate_after_per_hour'])}/h，"
            f"降幅 {_v(p['rate_reduction_pct'])}%{markers}"
        )
    lines.append(f"下次复评：{_ts(r['next_review_at'])}（届时已观测 {r['observed_h_at_next_review']} 小时）")
    if r["escalation_suggested"]:
        lines.append("结论为恶化：建议调用升级建议（recommend_escalation）判断是否升级为应急响应。")
    lines.append(f"依据：效果评估快照 {snapshot_id}（workflow 计算结果）；判据为开发占位值")
    return "\n".join(lines)


def escalation(r: dict[str, Any], snapshot_id: str) -> str:
    lines = [
        f"【升级建议】{r['panel']} 工作面 {_ts(r['as_of'])}",
        "结论：" + ("建议升级为应急响应（计算结果）" if r["recommend"] else "暂不建议升级为应急响应（计算结果）"),
        f"预警等级：{r['level_name']}；自燃发展阶段：{r['stage_name']}",
    ]
    if r["recommend"]:
        lines.append("命中的升级判据：")
        lines += [f"- {x}" for x in r["reasons"]]
        lines.append(r["evacuation_reminder"])
    else:
        lines.append("升级判据均未命中：")
        lines += [f"- {x}" for x in r["not_met"]]
    lines.append("规程依据（条文原文）：")
    lines += [f"- {a['document']} {a['article']}：{a['content']}" for a in r["articles"]]
    lines.append(f"依据：升级建议快照 {snapshot_id}（workflow 计算结果）；升级判据为开发占位值，是否升级由指挥人员决定")
    return "\n".join(lines)


def dispatch_draft(r: dict[str, Any], snapshot_id: str) -> str:
    lines = [f"【调度单草案】方案 {r['plan_id']}，目的地 {r['destination_node']}（{r['destination_from']}）"]
    if r["order"]:
        lines.append(f"调度单 {r['order']['order_id']}（草案，下发须经人工确认）：")
        for ln in r["order"]["lines"]:
            lines.append(
                f"- {ln['item_type']} {ln['quantity']} {ln['unit']}：{ln['from_node']} → {ln['to_node']}，预计 {_v(ln['eta_min'])} 分钟"
            )
    else:
        lines.append("调度单：没有可从库存调运的物资")
    lines.append("需求与库存（计算结果）：")
    for s in r["supply"]:
        lines.append(
            f"- {s['item_type']}：需要 {s['required']}，现有 {s['on_hand']}，缺口 {s['shortage']}"
            + ("" if s["in_ledger"] else "（台账没有这类物资）")
        )
    if r["nitrogen_capacity"]:
        c = r["nitrogen_capacity"]
        lines.append(f"制氮产能：需要 {c['required']} m³/h，现有 {c['on_hand']} m³/h，缺口 {c['shortage']}")
    if r["shortages"]:
        lines.append("库存不足的物资需人工联系外部来源：" + "、".join(s["item_type"] for s in r["shortages"]))
    if r["notes"]:
        lines.append("说明：")
        lines += [f"- {n}" for n in r["notes"]]
    lines.append(f"依据：调度草案快照 {snapshot_id}（workflow 计算结果）；用量参数为开发占位值")
    return "\n".join(lines)


def timeline(r: dict[str, Any], snapshot_id: str | None = None) -> str:
    span = f"{_ts(r['start']) if r['start'] else '开始'} 至 {_ts(r['end']) if r['end'] else '现在'}"
    lines = [f"【复盘时间线】{r['panel']} 工作面 {span}（矿井时间）"]
    lines += [f"- {_ts(x['mine_time'])} [{x['source']}] {x['event']}" + (f"（{x['ref']}）" if x["ref"] else "") for x in r["timeline"]]
    if not r["timeline"]:
        lines.append("- 无记录")
    if r["audit"]:
        lines.append("审计记录（系统时间，含被拦截和被拒绝的调用）：")
        for a in r["audit"]:
            lines.append(f"- {a['system_time']} {a['tool']} {a['outcome']}" + (f"：{a['note']}" if a["note"] else ""))
    return "\n".join(lines)
