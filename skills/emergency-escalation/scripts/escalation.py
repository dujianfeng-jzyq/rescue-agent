"""是否建议升级为应急响应（由 mcp/workflow 的 escalation.recommend 迁移，判据不变）。

输入：{"warning": <fire-assessment/warning.py 的输出>, "stage": <stage.py 的输出>, "alarms": <alarms.py 的输出>,
       "effect": <effect-review 的输出，可省略>}。
输出 result：recommend、reasons（命中的判据）、not_met（未命中时逐项说明）、criteria、articles（条文原文）、撤人提醒。
是否升级由指挥人员决定；撤人不等待本建议。
"""

from _skill import load_reference, main, record_result

VERSION = "escalation/1.0.0"
LEVELS = ["none", "blue", "yellow", "orange", "red"]
NAMES = {"none": "无", "blue": "蓝色（关注）", "yellow": "黄色（预警）", "orange": "橙色（严重）", "red": "红色（危险）"}
EVACUATION = "一旦进入应急响应，撤人由调度员按预案立即执行，不等待本建议和任何研判结果。"


def article_texts(nums, ref):
    by_num = {a["article"]: a for a in ref["articles"]}
    missing = [n for n in nums if n not in by_num]
    if missing:
        raise ValueError(f"references/articles.yaml 没有这些条文：{'、'.join(missing)}")
    return [{"document": by_num[n]["document"], "article": n, "content": by_num[n]["text"], "source": "skill-reference"} for n in nums]


def run(data, _args):
    w = record_result(data, "warning")
    st = record_result(data, "stage")
    al = record_result(data, "alarms")
    eff = record_result(data, "effect") if data.get("effect") is not None else None
    cfg = load_reference("escalation.yaml")
    arts = load_reference("articles.yaml")
    criteria = {k: cfg[k] for k in ("level_at_least", "stages", "effect_worsening", "realtime_alarm", "articles")}

    reasons = []
    if LEVELS.index(w["level"]) >= LEVELS.index(cfg["level_at_least"]):
        reasons.append(f"预警等级 {w['level_name']}，达到 {NAMES[cfg['level_at_least']]}")
    if st["stage"] in cfg["stages"]:
        reasons.append(f"自燃发展阶段 {st['stage_name']}")
    if cfg.get("effect_worsening") and eff and eff.get("overall") == "worsening":
        reasons.append(f"最近一次效果评估（处置开始 {eff['since']}）结论为 {eff['overall_name']}")
    if cfg.get("realtime_alarm") and al["alarms"]:
        reasons += [f"实时红线报警 [{a['article']}] {a['sensor_id']}：{a['message']}" for a in al["alarms"]]
    not_met = []
    if not reasons:
        not_met.append(f"预警等级 {w['level_name']}，未达到 {NAMES[cfg['level_at_least']]}")
        not_met.append(f"自燃发展阶段 {st['stage_name']}")
        not_met.append("最近一次效果评估：" + (eff["overall_name"] if eff else "没有"))
        not_met.append("实时红线报警：无")
    result = {
        "panel": w["panel"],
        "as_of": w["as_of"],
        "recommend": bool(reasons),
        "reasons": reasons,
        "not_met": not_met,
        "criteria": criteria,
        "articles": article_texts(cfg["articles"], arts),
        "evacuation_reminder": EVACUATION,
        "level": w["level"],
        "level_name": w["level_name"],
        "stage_name": st["stage_name"],
    }
    warnings = ["升级判据为开发占位值，是否升级由指挥人员决定"] if cfg["status"] == "placeholder" else []
    return result, {**criteria, "status": cfg["status"]}, warnings


if __name__ == "__main__":
    main(VERSION, run)
