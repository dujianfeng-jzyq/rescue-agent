"""趋势：预警触发条件涉及的指标的来源测点（没有触发时取默认指标），窗口内首个与最新可用值、变化量、上升速率。

输入：{"evidence": <证据内容>, "warning": <warning.py 的输出>}。
输出 result：trends（每项 sensor_id、measurand、hours、first、latest、change、rate_per_hour、data_quality 等）、
data_quality_all（预警判定与各趋势的数据质量说明合并去重，报告用）。变化量照抄 change，不要自己相减。
"""

from _evidence import series
from _quality import check_series, slope_per_hour
from _skill import evidence, load_reference, main, record_result
from _warning import LABELS

VERSION = "trend/1.0.0"


def targets(w, cfg):
    """触发条件涉及的指标的来源测点；没有触发时取默认指标。同一测点同一参数只算一次。
    按指标的固定顺序（LABELS）取，不依赖记录里字典的键序（记录按规范化 JSON 存储时键会重排）。"""
    keys = [k for k in LABELS if k in w["sources"] and any(LABELS[k] in t for t in w["triggered"])]
    if not keys:
        keys = [k for k in cfg["default_trend_indicators"] if k in w["sources"]]
    out = []
    for k in keys:
        pair = (w["sources"][k]["sensor_id"], w["sources"][k]["measurand"])
        if pair not in out:
            out.append(pair)
    return out


def trend(ev, sensor_id, measurand, hours):
    rs = series(ev, sensor_id, measurand, hours)
    sibling = series(ev, sensor_id, "CO", hours) if measurand in ("C2H4", "C2H2") else None  # 恒 0 时用同测点 CO 判断是否卡值
    rep = check_series(f"{sensor_id}/{measurand}", rs, sibling)
    first, last = (rep.usable[0], rep.usable[-1]) if rep.usable else (None, None)
    rate = slope_per_hour(rep.usable)
    return {
        "sensor_id": sensor_id,
        "measurand": measurand,
        "hours": hours,
        "points": len(rs),
        "usable_points": len(rep.usable),
        "first": first["value"] if first else None,
        "first_ts": first["ts"] if first else None,
        "latest": last["value"] if last else None,
        "latest_ts": last["ts"] if last else None,
        "change": round(last["value"] - first["value"], 3) if first and last else None,
        "rate_per_hour": round(rate, 3) if rate is not None else None,
        "data_quality": rep.flags,
    }


def run(data, _args):
    ev = evidence(data)
    w = record_result(data, "warning")
    cfg = load_reference("assessment.yaml")
    trends = [trend(ev, sid, m, cfg["trend_hours"]) for sid, m in targets(w, cfg)]
    dq = list(dict.fromkeys(w["data_quality"] + [q for t in trends for q in t["data_quality"]]))
    params = {"trend_hours": cfg["trend_hours"], "default_trend_indicators": cfg["default_trend_indicators"], "status": cfg["status"]}
    return {"panel": ev["panel"], "as_of": ev["as_of"], "trends": trends, "data_quality_all": dq}, params, []


if __name__ == "__main__":
    main(VERSION, run)
