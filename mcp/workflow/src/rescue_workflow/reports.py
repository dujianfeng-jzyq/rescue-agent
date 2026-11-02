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
