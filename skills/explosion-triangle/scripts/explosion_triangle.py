"""火区混合气体爆炸危险性：多组分、温度补偿的爆炸三角形，输出分区和风险图（SVG）。

两种输入（二选一）：
- 按工作面：{"evidence": <证据内容>, "options": {"sensor_id"?: 只算这个束管测点, "temperature_sensor_id"?: 指定测温点}}。
  不给 sensor_id 时，本工作面全部同时测 O2、CO2 和可燃气体的束管测点逐个计算，取最不利（references/panel.yaml）；
  任一测点算不出来时总体为 unknown（按有爆炸危险对待）。每个测点用最近 sample_window_h 小时里同一时刻完整可用的一组读数，
  温度取同一位置的测温点（同一巷道、同一采空区深度或巷道位置）。
- 人工化验单：{"sample": {"o2_pct", "co2_pct", "temperature_c", "ch4_pct"?, "co_ppm"?, "c2h4_ppm"?, "c2h2_ppm"?,
  "c2h6_ppm"?, "h2_ppm"?, "n2_pct"?}}。O2、CO2 和温度必须是实测值；化验单没有的组分不填。

由 mcp/fire-model 的 tube.py、explosion.py、chart.py 和 mcp/workflow 的 assess.explosion_panel 迁移，算法不变。
风险图画的是最不利测点（或指定测点、化验单）；结论以 result 的 zone 为准。
"""

from datetime import datetime
from html import escape

from _chart import render_svg
from _evidence import sensors, series
from _explosion import Sample, analyze, load_constants
from _quality import check_series
from _skill import load_reference, main

VERSION = "explosion-triangle/1.0.0"
COMBUSTIBLE = {"CH4": "CH4", "CO": "CO", "C2H4": "C2H4", "C2H2": "C2H2", "C2H6": "C2H6", "H2": "H2"}
MARKERS = ("C2H4", "C2H2", "C2H6", "H2")  # 未出现前读数就是 0
UNITS = {"CO": "ppm", "CO2": "%", "CH4": "%", "O2": "%", "N2": "%", "C2H4": "ppm", "C2H2": "ppm", "C2H6": "ppm", "H2": "ppm"}
OVERALL_NAMES = {
    "explosive": "处于爆炸范围",
    "may_become_explosive": "可能进入爆炸范围（减风积聚或混入空气）",
    "not_explosive": "当前不在爆炸范围",
    "unknown": "无法判定（有测点算不出来，按有爆炸危险对待）",
}


def to_pct(m, value):
    return value / 10_000 if UNITS[m] == "ppm" else value


def pair_temperature(sen, by_id, chosen):
    """找与气体测点同一位置的测温点：同一巷道，且采空区深度或巷道位置相同。找不到唯一的就报错并列出候选。"""
    candidates = [s for s in by_id.values() if "TEMP" in s["measurands"] and s["roadway_id"] == sen["roadway_id"]]
    if chosen is not None:
        t = by_id.get(chosen)
        if t is None or "TEMP" not in t["measurands"]:
            raise ValueError(f"{chosen} 不是测温点")
        return chosen
    same = [
        s for s in candidates
        if (sen.get("goaf_depth_m") is not None and s.get("goaf_depth_m") == sen["goaf_depth_m"])
        or (sen.get("position_m") is not None and s.get("position_m") == sen["position_m"])
    ]  # fmt: skip
    if len(same) == 1:
        return same[0]["id"]
    listed = "、".join(f"{s['id']}（{s['name']}）" for s in candidates) or "无"
    raise ValueError(f"找不到与 {sen['id']} 同一位置的唯一测温点，请用 temperature_sensor_id 指定。同一巷道的测温点：{listed}")


def assess_tube(ev, sensor_id, xc, temperature_sensor_id=None):
    """用测点最近一次完整的气体分析判定爆炸危险性。数据问题抛 ValueError（说明写明原因）。返回（结果，风险图副标题）。"""
    by_id = {s["id"]: s for s in sensors(ev)}
    sen = by_id.get(sensor_id)
    if sen is None:
        raise ValueError(f"证据里没有测点：{sensor_id}")
    fuels = [m for m in sen["measurands"] if m in COMBUSTIBLE]
    lacking = [m for m in ("O2", "CO2") if m not in sen["measurands"]]
    if lacking or not fuels:
        raise ValueError(f"测点 {sensor_id} 不测 {'、'.join(lacking) or '可燃气体'}，不能做爆炸三角形；请用测 O2、CO2 和可燃气体的束管测点")
    temp_id = sensor_id if "TEMP" in sen["measurands"] else pair_temperature(sen, by_id, temperature_sensor_id)

    window = xc.sample_window_h
    gas_ms = [*fuels, "O2", "CO2", *(["N2"] if "N2" in sen["measurands"] else [])]
    ser = {m: series(ev, sensor_id, m, window) for m in gas_ms}
    flags = []
    usable = {}
    for m in gas_ms:
        sibling = ser.get("CO") if m in MARKERS else None  # 与预警判定一致：标志气体恒 0 而 CO 在变按未检出
        rep = check_series(f"{sensor_id}/{m}", ser[m], sibling)
        flags += rep.flags
        usable[m] = {r["ts"]: r["value"] for r in rep.usable}
    required = [m for m in gas_ms if m != "N2"]
    common = set.intersection(*(set(usable[m]) for m in required))
    if not common:
        raise ValueError(
            f"测点 {sensor_id} 最近 {window:g} 小时没有同一时刻完整可用的一组读数（需 {'、'.join(required)}），"
            f"不能计算。数据质量：{'；'.join(flags) or '无可用读数'}"
        )
    ts = max(common, key=datetime.fromisoformat)
    t_ts = datetime.fromisoformat(ts)
    n2 = usable["N2"].get(ts) if "N2" in usable else None

    trep = check_series(f"{temp_id}/TEMP", series(ev, temp_id, "TEMP", window))
    flags += trep.flags
    temps = [r for r in trep.usable if datetime.fromisoformat(r["ts"]) <= t_ts] or trep.usable
    if not temps:
        raise ValueError(f"测温点 {temp_id} 最近 {window:g} 小时没有可用温度读数，不能计算（不按假定温度代替）")
    temp = min(temps, key=lambda r: abs((datetime.fromisoformat(r["ts"]) - t_ts).total_seconds()))

    out = analyze(
        Sample(
            combustibles={COMBUSTIBLE[m]: to_pct(m, usable[m][ts]) for m in fuels},
            o2=usable["O2"][ts],
            co2=usable["CO2"][ts],
            temperature_c=temp["value"],
            n2=n2,
        ),
        xc,
    )
    out["sample"] = {
        "sensor_id": sensor_id,
        "sensor_name": sen["name"],
        "ts": ts,
        "readings": {m: usable[m][ts] for m in required} | ({"N2": n2} if n2 is not None else {}),
        "units": {m: UNITS[m] for m in required},
        "temperature_sensor_id": temp_id,
        "temperature_ts": temp["ts"],
    }
    out["data_quality"] = flags
    subtitle = f"{sen['name']}（{sensor_id}）· 取样时刻 {t_ts:%Y-%m-%d %H:%M} · 温度取自 {temp_id}"
    return out, subtitle


def panel_tubes(ev):
    """本工作面全部同时测 O2、CO2 和可燃气体的束管测点。"""
    panel = ev["panel"]
    return [
        s["id"] for s in sensors(ev)
        if panel in s["id"] and s["source"] == "bundle_tube"
        and {"O2", "CO2"} <= set(s["measurands"]) and set(s["measurands"]) & set(COMBUSTIBLE)
    ]  # fmt: skip


def from_evidence(ev, options, xc, order):
    only = options.get("sensor_id")
    ids = [only] if only else panel_tubes(ev)
    points, failures, subtitles = [], [], {}
    for sid in ids:
        try:
            r, subtitles[sid] = assess_tube(ev, sid, xc, options.get("temperature_sensor_id"))
        except ValueError as exc:
            failures.append({"sensor_id": sid, "error": str(exc)})
            continue
        points.append({"sensor_id": sid, **r})
    worst = min(points, key=lambda p: order.index(p["explosion_risk"])) if points else None
    overall = "unknown" if failures or not points else worst["explosion_risk"]
    result = {
        "mode": "sensor" if only else "panel",
        "panel": ev["panel"],
        "as_of": ev["as_of"],
        "overall": overall,
        "overall_name": OVERALL_NAMES[overall],
        "worst_sensor_id": worst["sensor_id"] if worst else None,
        "points": points,
        "failures": failures,
        "chart_sensor_id": worst["sensor_id"] if worst else None,
    }
    return result, (worst, subtitles.get(worst["sensor_id"]) if worst else None)


def from_sample(given_sample, xc):
    s = dict(given_sample)
    for k in ("o2_pct", "co2_pct", "temperature_c"):
        if s.get(k) is None:
            raise ValueError(f"化验单缺少 {k}：O2、CO2 和温度必须是实测值，不能估计")
    given = {g: s.get(k) for g, k in (("CH4", "ch4_pct"), ("H2", "h2_ppm"), ("CO", "co_ppm"), ("C2H4", "c2h4_ppm"), ("C2H2", "c2h2_ppm"), ("C2H6", "c2h6_ppm"))}
    ppm = {"CO", "C2H4", "C2H2", "C2H6", "H2"}
    combustibles = {g: (v / 10_000 if g in ppm else v) for g, v in given.items() if v is not None}
    out = analyze(Sample(combustibles=combustibles, o2=s["o2_pct"], co2=s["co2_pct"], temperature_c=s["temperature_c"], n2=s.get("n2_pct")), xc)
    missing = [g for g, v in given.items() if v is None]
    if missing:
        out["warnings"].append(f"化验单未提供 {'、'.join(missing)}，未计入；这些组分实际存在时结论偏乐观")
    keys = ("o2_pct", "co2_pct", "temperature_c", "n2_pct", "ch4_pct", "h2_ppm", "co_ppm", "c2h4_ppm", "c2h2_ppm", "c2h6_ppm")
    out["sample"] = {"source": "人工取样化验单", "inputs": {k: s[k] for k in keys if s.get(k) is not None}}
    point = {"sensor_id": None, **out}
    result = {
        "mode": "sample",
        "panel": None,
        "as_of": None,
        "overall": out["explosion_risk"],
        "overall_name": OVERALL_NAMES[out["explosion_risk"]],
        "worst_sensor_id": None,
        "points": [point],
        "failures": [],
        "chart_sensor_id": None,
    }
    return result, (point, "人工取样化验单")


def failure_svg(result):
    lines = [f"{f['sensor_id']}：{f['error']}" for f in result["failures"]] or ["没有可计算的测点"]
    body = "".join(f'<text x="24" y="{96 + 24 * i}" font-size="13">{escape(x[:90])}</text>' for i, x in enumerate(lines[:12]))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="420" viewBox="0 0 1000 420" role="img" aria-label="火区混合气体爆炸三角形">'
        '<rect width="1000" height="420" fill="#FFFFFF"/>'
        '<text x="24" y="40" font-size="22" font-weight="700">火区混合气体爆炸三角形：无法判定</text>'
        f'<text x="24" y="66" font-size="13" fill="#B42318">{escape(result["overall_name"])}</text>{body}</svg>\n'
    )


def run(data, args):
    raw = load_reference("explosion.yaml")
    order = load_reference("panel.yaml")["explosion_order"]
    xc = load_constants(raw)
    if "sample" in data:
        result, (point, subtitle) = from_sample(data["sample"], xc)
    elif "evidence" in data:
        ev = data["evidence"]
        if not isinstance(ev, dict) or "groups" not in ev:
            raise ValueError("evidence 应为证据内容")
        result, (point, subtitle) = from_evidence(ev, data.get("options") or {}, xc, order)
    else:
        raise ValueError("输入要有 evidence（按工作面或测点）或 sample（人工化验单）")
    svg = render_svg(point, xc.air_o2, subtitle) if point else failure_svg(result)
    args.svg_out.write_text(svg, encoding="utf-8")
    params = {k: v for k, v in raw.items() if k != "reviewed_by"} | {"explosion_order": order}
    warnings = ["顶点、温度修正系数等参数为开发占位值，待专家审定；结果用于风险研判，不替代现场检测和指挥决策"] if raw["status"] == "placeholder" else []
    return result, params, warnings


if __name__ == "__main__":
    main(VERSION, run, svg=True)
