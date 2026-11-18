"""外部火灾推演服务（火灾蔓延：外部为主、我方兜底）的请求，用我方编号（docs/design-v7-fire-sim.md 第 4 节、附录 A.2）。

输入：{"evidence": <证据内容，含 network>, "warning": <warning.py 的输出>}。
输出：request（交给 fire_sim 适配服务的参数）、火源所在巷道和位置、所用占位参数。
预警等级未达到 fire_sim.yaml 的 level_at_least 时 needed 为 false（不调用服务）。本脚本不访问网络。

服务模拟的是巷道内的明火（火源、燃烧面积、燃料），不是采空区阴燃：采空区自燃按“漏风通道靠上隅角一端起火”近似，结果是试验性的。
快速接入阶段的输入全部是占位或估算：
- 坐标：与路径规划服务请求相同的占位坐标（_layout.py：单位圆布局 + 每条巷道一个中间节点，两段之和等于巷道长度，z = 0）。
  服务按坐标计算巷道长度；z = 0 时火风压（浮力）不起作用。
- 每条巷道拆成两段（_route.py 的编号约定），风阻各取一半，断面取占位值；没有风阻的巷道用占位风阻。
- 通风边界：压入式。从地面出口出发的巷道是进风边界，风量 = 这些巷道设计风量之和；到达地面出口的巷道是出风边界。
"""

from _layout import layout, mid_point, rnd
from _route import mid_node
from _skill import evidence, load_reference, main, record_result
from _warning import NAMES, level_at_least

VERSION = "fire_sim_request/1.0.0"
METHOD = "占位坐标（同路径规划服务请求，z = 0）+ 占位断面和风阻 + 设计风量作进风量 + 火源放在漏风通道上（试验性）"


def fire_roadway(net, panel, kind):
    for r in net["roadways"]:
        if r["kind"] == kind and panel in r["id"]:
            return r
    raise ValueError(f"网络中找不到工作面 {panel} 的 {kind} 巷道，不能确定火源位置")


def build(net, p, section, notes):
    roadways = net["roadways"]
    used = {r["from_node"] for r in roadways} | {r["to_node"] for r in roadways}
    pos = layout(used, roadways)
    surface = {n["id"] for n in net["nodes"] if n.get("is_surface")}
    nodes = [{"id": nid, "xyz": [rnd(pos[nid][0]), rnd(pos[nid][1]), 0.0]} for nid in sorted(used)]
    branches, inlet = [], {}
    for r in roadways:
        m = mid_point(pos[r["from_node"]], pos[r["to_node"]], r["length_m"])
        nodes.append({"id": mid_node(r["id"]), "xyz": [rnd(m[0]), rnd(m[1]), 0.0]})
        res = r.get("resistance_ns2_m8")
        if not res:
            res = p["default_resistance_ns2_m8"]
            notes.append(f"巷道 {r['id']} 没有风阻，按占位值 {res} N·s²/m⁸")
        area = p["goaf_section_m2"] if r["kind"] == "goaf" else section
        common = {"resistance": rnd(res / 2), "area_m2": area}
        a = {
            "id": r["id"] + "#a",
            "from_node": r["from_node"],
            "to_node": mid_node(r["id"]),
            **common,
            "boundary": None,
        }
        b = {
            "id": r["id"] + "#b",
            "from_node": mid_node(r["id"]),
            "to_node": r["to_node"],
            **common,
            "boundary": None,
        }
        if r["from_node"] in surface:
            a["boundary"] = "inlet"
            if not r.get("design_airflow_m3_min"):
                raise ValueError(f"进风巷道 {r['id']} 没有设计风量，不能确定进风量")
            inlet[r["from_node"]] = inlet.get(r["from_node"], 0.0) + r["design_airflow_m3_min"] / 60
        if r["to_node"] in surface:
            b["boundary"] = "outlet"
        branches += [a, b]
    if not inlet:
        raise ValueError("网络里没有从地面出口出发的巷道，不能确定进风边界")
    if not any(b["boundary"] == "outlet" for b in branches):
        raise ValueError("网络里没有到达地面出口的巷道，不能确定出风边界")
    return nodes, branches, {k: rnd(v) for k, v in sorted(inlet.items())}


def run(data, _args):
    ev = evidence(data)
    w = record_result(data, "warning")
    p = load_reference("fire_sim.yaml")
    section = load_reference("personnel.yaml")["default_section_m2"]
    params = {
        **{k: v for k, v in p.items() if k != "reviewed_by"},
        "default_section_m2": section,
    }
    base = {
        "panel": ev["panel"],
        "as_of": ev["as_of"],
        "experimental": True,
        "method": METHOD,
    }
    floor = p["level_at_least"]
    if not level_at_least(w["level"], floor):
        return (
            {
                **base,
                "needed": False,
                "note": f"预警等级未达到{NAMES[floor]}，不请求火灾推演",
                "request": None,
            },
            params,
            [],
        )
    if "network" not in ev:
        raise ValueError("证据里没有通风网络（采集清单 assessment 应取 network）")
    net = ev["network"]
    notes: list[str] = []
    nodes, branches, inlet = build(net, p, section, notes)
    src = fire_roadway(net, ev["panel"], p["fire_source_kind"])
    pos = p["fire_position"]
    # 火源落在拆开后的哪一段：前半段 #a、后半段 #b，段内位置按比例换算
    seg, seg_pos = (src["id"] + "#a", pos * 2) if pos < 0.5 else (src["id"] + "#b", pos * 2 - 1)
    fire = {
        "branch": seg, "position": rnd(seg_pos), "total_time_s": p["total_time_s"], "output_interval_s": p["output_interval_s"],
        "fuel_type": p["fuel_type"], "burning_area_m2": p["burning_area_m2"], "fuel_load_kg": p["fuel_load_kg"], "oxygen_pct": p["oxygen_pct"],
    }  # fmt: skip
    request = {
        "nodes": nodes,
        "branches": branches,
        "fire": fire,
        "boundary": {
            "mode": "forced",
            "inlet_flows_m3_s": inlet,
            "outlet_pressures_pa": {},
        },
    }
    warnings = [
        "外部服务模拟巷道内明火，输入使用占位坐标、断面、风阻和火源参数，结果是试验性的；推演可用时界面只显示推演结果",
        *notes,
    ]
    out = {
        **base,
        "needed": True,
        "note": None,
        "fire_roadway": src["id"],
        "fire_roadway_name": src["name"],
        "fire_position": pos,
        "request": request,
    }
    return out, params, warnings


if __name__ == "__main__":
    main(VERSION, run)
