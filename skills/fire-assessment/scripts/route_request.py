"""外部路径规划服务（避灾路线：外部为主、我方兜底）的请求：每组受威胁人员一个，用我方编号（docs/design-v6-route-service.md 第 4 节、附录 A.2）。

输入：{"evidence": <证据内容，含 network>, "personnel": <personnel.py 的输出>}。
输出：requests（每组受威胁人员：所在巷道、人数、服务请求），由证据插件交给 route_planner 适配服务逐个调用。
personnel 未计算或没有受威胁人员时 needed 为 false（不调用服务）。本脚本不访问网络。

快速接入阶段的输入全部是占位或估算（结果标明“试验性”）：
- 坐标：我方网络没有坐标。节点按编号排在单位圆上，整体缩放到每条巷道两端的直线距离都不超过巷道长度；
  每条巷道在中垂线上加一个中间节点，使两段折线之和等于巷道长度。z 一律取 0（不引入编造的坡度）。图形本身没有意义。
- 断面：统一占位值；风速 = 设计风量 ÷ 60 ÷ 断面（没有设计风量用缺省风速），方向按我方约定（起点流向终点），为正值。
- 烟雾：我方算出的受污染巷道（personnel 的 contaminated_roadways）取占位值，其余为 0。
- 起点：人员所在巷道的中间节点；终点：网络里的地面出口和避难硐室。采空区漏风通道不作为可通行巷道。
"""

from _layout import layout, mid_point, rnd
from _route import mid_node
from _skill import evidence, load_reference, main, record_result

VERSION = "route_request/1.0.0"
METHOD = "占位坐标（单位圆布局，按巷道长度加中间节点，z = 0）+ 占位断面 + 设计风量估算风速 + 受污染巷道取固定烟雾浓度（试验性）"


def wind_speed(r, section_m2, p_personnel, limit, notes):
    flow = r.get("design_airflow_m3_min")
    v = max(p_personnel["min_air_velocity_m_s"], flow / 60 / section_m2) if flow else p_personnel["default_air_velocity_m_s"]
    if v > limit:
        notes.append(f"巷道 {r['id']} 的估算风速 {round(v, 2)} m/s 超出服务范围，按 {limit} m/s 传入")
        v = limit
    return rnd(v)


def build_network(net, contaminated, p, p_personnel, notes):
    roadways = [r for r in net["roadways"] if r["kind"] != "goaf"]
    used = {r["from_node"] for r in roadways} | {r["to_node"] for r in roadways}
    pos = layout(used, roadways)
    section = p["section_height_m"] * p["section_width_m"]
    nodes = [{"id": nid, "xyz": [rnd(pos[nid][0]), rnd(pos[nid][1]), 0.0]} for nid in sorted(used)]
    edges = []
    for r in roadways:
        m = mid_point(pos[r["from_node"]], pos[r["to_node"]], r["length_m"])
        nodes.append({"id": mid_node(r["id"]), "xyz": [rnd(m[0]), rnd(m[1]), 0.0]})
        common = {
            "height": p["section_height_m"], "width": p["section_width_m"],
            "wind_speed": wind_speed(r, section, p_personnel, p["wind_speed_limit_m_s"], notes),
            "smoke": p["smoke_concentration_contaminated"] if r["id"] in contaminated else 0,
        }  # fmt: skip
        edges.append({"id": r["id"] + "#a", "source": r["from_node"], "target": mid_node(r["id"]), **common})
        edges.append({"id": r["id"] + "#b", "source": mid_node(r["id"]), "target": r["to_node"], **common})
    targets = sorted(n["id"] for n in net["nodes"] if (n.get("is_surface") or n.get("is_refuge")) and n["id"] in used)
    return nodes, edges, targets


def run(data, _args):
    ev = evidence(data)
    per = record_result(data, "personnel")
    p = load_reference("route_service.yaml")
    p_personnel = load_reference("personnel.yaml")
    params = {
        **{k: v for k, v in p.items() if k != "reviewed_by"},
        "walk_speed_m_s": p_personnel["walk_speed_m_s"],
        "default_air_velocity_m_s": p_personnel["default_air_velocity_m_s"],
        "min_air_velocity_m_s": p_personnel["min_air_velocity_m_s"],
    }
    base = {"panel": ev["panel"], "as_of": ev["as_of"], "experimental": True, "method": METHOD}
    if not per.get("computed"):
        return {**base, "needed": False, "note": "未计算受威胁人员，不请求外部路径规划服务", "requests": []}, params, []
    if not per["threatened"]:
        return {**base, "needed": False, "note": "没有受威胁人员，不请求外部路径规划服务", "requests": []}, params, []
    if "network" not in ev:
        raise ValueError("证据里没有通风网络（采集清单 assessment 应取 network）")
    contaminated = {c["roadway_id"] for c in per["contaminated_roadways"]}
    notes: list[str] = []
    nodes, edges, targets = build_network(ev["network"], contaminated, p, p_personnel, notes)
    if not targets:
        return {**base, "needed": False, "note": "网络里没有地面出口或避难硐室，不能请求外部路径规划服务", "requests": []}, params, notes
    by_id = {r["id"] for r in ev["network"]["roadways"] if r["kind"] != "goaf"}
    requests = []
    for g in per["threatened"]:
        if g["roadway_id"] not in by_id:
            raise ValueError(f"受威胁人员所在巷道 {g['roadway_id']} 不在通风网络的可通行巷道里")
        requests.append({
            "roadway_id": g["roadway_id"], "count": g["count"],
            "request": {
                "nodes": nodes, "edges": edges, "start": mid_node(g["roadway_id"]), "targets": targets,
                "walk_speed": p_personnel["walk_speed_m_s"], "max_smoke_time": p["max_smoke_time_s"],
            },
        })  # fmt: skip
    warnings = ["外部服务的输入使用占位坐标、断面和烟雾浓度，结果是试验性的；服务可用时界面和撤人通知用服务的路线", *notes]
    return {**base, "needed": True, "note": None, "requests": requests}, params, warnings


if __name__ == "__main__":
    main(VERSION, run)
