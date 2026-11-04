"""阶段“研判说明”的选择清单：按本次研判结果列出可选的关注点（事实）和下一步（references/explanation.yaml）。

模型只能从这里选（选项编号），说明文字由代码检查；关注点的文字照抄各结果记录，不另算数。
输入：{"warning", "stage", "alarms", "trend", "explosion", "personnel", "escalation"}，各为相应脚本的输出。
输出 result：focus_options、next_step_options（每项 id、label）、limits。
"""

from _skill import load_reference, main, record_result

VERSION = "explanation-options/1.0.0"
NAMES = ["warning", "stage", "alarms", "trend", "explosion", "personnel", "escalation"]


def focus_options(r):
    w, st, al, tr, x, pe, es = (r[n] for n in NAMES)
    out = [{"id": "alarm:" + f"{a['rule_id']}@{a['sensor_id']}", "label": f"实时报警 [{a['article']}] {a['sensor_id']}：{a['message']}"} for a in al["alarms"]]
    out.append({"id": "level", "label": f"预警等级 {w['level_name']}" + (f"（{'；'.join(w['triggered'])}）" if w["triggered"] else "")})
    out.append({"id": "stage", "label": f"自燃发展阶段 {st['stage_name']}（{'；'.join(st['stage_basis'])}）"})
    for t in tr["trends"]:
        out.append({
            "id": f"trend:{t['sensor_id']}/{t['measurand']}",
            "label": f"{t['sensor_id']} {t['measurand']} 近 {t['hours']} 小时 {t['first']} → {t['latest']}，变化 {t['change']}，速率 {t['rate_per_hour']}/h",
        })  # fmt: skip
    out.append({"id": "explosion", "label": f"爆炸危险性（总体）{x['overall_name']}"})
    for p in x["points"]:
        if p["zone"] != "IV":
            out.append({"id": f"explosion:{p['sensor_id']}", "label": f"{p['sensor_id']} {p['zone']} 区 {p['zone_name']}：{p['meaning']}"})
    for f in x["failures"]:
        out.append({"id": f"explosion-failure:{f['sensor_id']}", "label": f"{f['sensor_id']} 未能计算爆炸三角形：{f['error']}"})
    if pe["computed"]:
        groups = "；".join(f"{g['roadway_id']} {g['count']} 人" for g in pe["threatened"])
        out.append({"id": "personnel", "label": f"受威胁 {pe['total_threatened']} 人（{groups}）" if groups else f"受威胁 {pe['total_threatened']} 人"})
    else:
        out.append({"id": "personnel", "label": pe["note"]})
    out += [{"id": f"data-quality:{i}", "label": f"数据质量：{q}"} for i, q in enumerate(tr["data_quality_all"])]
    out += [{"id": f"note:{i}", "label": f"说明：{n}"} for i, n in enumerate(w["notes"])]
    out.append({
        "id": "escalation",
        "label": ("建议升级为应急响应：" + "；".join(es["reasons"])) if es["recommend"] else "暂不建议升级为应急响应（升级判据均未命中）",
    })  # fmt: skip
    return out


def conditions(r):
    w, st, al, x, es = r["warning"], r["stage"], r["alarms"], r["explosion"], r["escalation"]
    zones = {p["zone"] for p in x["points"]}
    return {
        "alarm_or_escalation": bool(al["alarms"]) or es["recommend"],
        "escalation_recommended": es["recommend"],
        "level_not_none": w["level"] != "none",
        "any_sign": w["level"] != "none" or st["stage"] not in ("no_sign", "unknown"),
        "data_problem": bool(r["trend"]["data_quality_all"]) or bool(x["failures"]) or st["stage"] == "unknown",
        "explosion_failure": bool(x["failures"]),
        "zone_ii": "II" in zones,
        "zone_iii": "III" in zones,
        "accelerating_or_worse": st["stage"] in ("accelerating", "intense") or w["level"] in ("orange", "red"),
        "severe": w["level"] == "red" or st["stage"] == "intense",
        "no_sign": w["level"] == "none" and st["stage"] == "no_sign" and not al["alarms"],
    }


def run(data, _args):
    r = {n: record_result(data, n) for n in NAMES}
    cfg = load_reference("explanation.yaml")
    cond = conditions(r)
    unknown = sorted({s["when"] for s in cfg["next_steps"]} - set(cond))
    if unknown:
        raise ValueError(f"references/explanation.yaml 用了未定义的条件：{'、'.join(unknown)}")
    steps = [{"id": s["id"], "label": s["label"]} for s in cfg["next_steps"] if cond[s["when"]]]
    result = {
        "panel": r["warning"]["panel"],
        "as_of": r["warning"]["as_of"],
        "focus_options": focus_options(r),
        "next_step_options": steps,
        "limits": cfg["limits"],
    }
    return result, {"limits": cfg["limits"], "conditions": cond, "status": cfg["status"]}, []


if __name__ == "__main__":
    main(VERSION, run)
