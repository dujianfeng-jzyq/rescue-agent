"""外部路径规划服务的结果（第二意见）与我方避灾路线的比较（docs/design-v6-route-service.md 附录 A.3、A.4）。

输入：{"evidence": <证据内容，含 network>, "personnel": <personnel.py 的输出>, "route_request": <route_request.py 的输出>,
       "routes": <证据插件调用 route_planner 得到的证据内容：{results: [{roadway_id, result | error}], error}>}。
输出：每组受威胁人员的第二意见路线（巷道序列、终点、当量距离、烟中时间、暂时脱险时间）和与我方路线的比较（情况、终点是否相同、
巷道重合比例、长度差）。比较结论全部在这里算好，照抄，不要自己判断“哪条更好”：两者不一致时都显示，由人判断。

两种算法语义不同：我方受污染巷道一律不走，不可达就“需人工研判”；服务允许穿过有烟巷道，只要烟中时间不超过上限。
所以“我方无路、服务给出穿烟路线”是预期中会出现的情况，单独标出（ours_none_service_through_smoke），不能说成“服务找到了路”。
巷道序列不含人员所在巷道本身，节点序列从人员所在巷道的一端开始（都与我方路线一致）；长度按巷道实际长度计，人员所在巷道算一半。
"""

from _route import plain_nodes, roadways_of
from _skill import evidence, main, record_result

VERSION = "route_compare/1.0.0"
METHOD = "外部服务结果换回我方巷道编号后与我方路线比较；重合比例 = 共同巷道数 ÷ 两条路线巷道的并集数（试验性）"

CASES = {
    "service_unavailable": "第二意见不可用",
    "both_none": "两种方法都没有给出路线，需人工研判",
    "ours_none_service_through_smoke": "我方避开受污染巷道后无路可走；外部服务给出穿过有烟巷道的参考路线，能否穿烟由人判断",
    "ours_none_service_clean": "我方无路可走，外部服务给出不经过受污染巷道的路线（需核对我方算法）",
    "ours_route_service_none": "外部服务没有给出路线（不可达或烟中时间超过上限，服务目前不区分）",
    "same_route": "两种路线相同",
    "same_target": "终点相同、路线不同",
    "different_target": "终点不同",
}


def route_length(roadways, start_roadway, lengths):
    return round(sum(lengths[r] for r in roadways) + lengths[start_roadway] / 2)


def service_side(group, entry, contaminated, lengths, names):
    if entry is None:
        return {"status": "unavailable", "error": "没有调用结果"}
    if entry.get("error"):
        return {"status": "unavailable", "error": entry["error"]}
    res = entry["result"]
    per_target = [{"target": t["target"], "status": t["status"]} for t in res.get("targets", [])]
    if not res.get("available"):
        return {"status": "unavailable", "error": res.get("error") or "服务不可用", "per_target": per_target}
    best = res.get("best")
    if not best:
        return {"status": "no_route", "error": res.get("error"), "per_target": per_target}
    roadways = roadways_of(best["edges"])
    if roadways and roadways[0] == group["roadway_id"]:
        roadways = roadways[1:]
    return {
        "status": "route", "error": res.get("error"), "per_target": per_target,
        "target": best["target"], "target_name": names.get(best["target"], best["target"]),
        "nodes": plain_nodes(best["nodes"]), "roadways": roadways, "length_m": route_length(roadways, group["roadway_id"], lengths),
        "effective_distance_m": round(best["effective_distance_m"]),
        "smoke_time_s": round(best["smoke_time_s"]),
        "temporary_escape_time_s": round(best["temporary_escape_time_s"]),
        "smoke_roadways": [r for r in roadways if r in contaminated],
        "ambiguous": bool(best.get("ambiguous_hops")),
    }  # fmt: skip


def compare(ours, svc):
    out = {"case": None, "same_target": None, "same_route": None, "roadway_overlap": None, "length_diff_m": None}
    has_ours = bool(ours.get("reachable"))
    if svc["status"] == "unavailable":
        out["case"] = "service_unavailable"
    elif svc["status"] == "no_route":
        out["case"] = "ours_route_service_none" if has_ours else "both_none"
    elif not has_ours:
        out["case"] = "ours_none_service_through_smoke" if svc["smoke_roadways"] else "ours_none_service_clean"
    else:
        a, b = set(ours["roadways"]), set(svc["roadways"])
        union = a | b
        out.update(
            same_target=ours["target"] == svc["target"],
            same_route=ours["roadways"] == svc["roadways"],
            roadway_overlap=round(len(a & b) / len(union), 2) if union else 1.0,
            length_diff_m=svc["length_m"] - ours["length_m"],
        )
        out["case"] = "same_route" if out["same_route"] else ("same_target" if out["same_target"] else "different_target")
    out["case_text"] = CASES[out["case"]]
    return out


def text_line(g, ours, svc, cmp_):
    head = f"{g['roadway_id']}（{g['count']} 人）：{cmp_['case_text']}"
    if svc["status"] == "unavailable":
        return f"{head}：{svc['error']}"
    if svc["status"] != "route":
        return head
    detail = f"参考路线经 {' → '.join(svc['roadways']) or '本巷道'} 到{svc['target_name']}，约 {svc['length_m']} 米"
    if svc["smoke_time_s"]:
        where = f"经过受污染巷道 {'、'.join(svc['smoke_roadways'])}" if svc["smoke_roadways"] else "只在人员所在巷道内"
        detail += f"，烟中通行约 {svc['smoke_time_s']} 秒（{where}）"
    return f"{head}。{detail}（外部服务，试验性）"


def run(data, _args):
    ev = evidence(data)
    per = record_result(data, "personnel")
    req = record_result(data, "route_request")
    routes = data.get("routes")
    base = {"panel": ev["panel"], "as_of": ev["as_of"], "experimental": True, "method": METHOD}
    if not req.get("needed"):
        return {**base, "computed": False, "status": "not_needed", "note": req.get("note"), "groups": [], "counts": {}, "lines": []}, {}, []
    if not isinstance(routes, dict) or not isinstance(routes.get("results"), list):
        raise ValueError("输入缺少外部服务的调用结果 routes（证据插件调用 route_planner 得到的证据内容，含 results）")
    net = ev["network"]
    lengths = {r["id"]: r["length_m"] for r in net["roadways"]}
    names = {n["id"]: n["name"] for n in net["nodes"]}
    contaminated = {c["roadway_id"] for c in per["contaminated_roadways"]}
    by_group = {r["roadway_id"]: r for r in routes["results"]}
    ours_by = {g["roadway_id"]: g for g in per["threatened"]}
    groups = []
    for want in req["requests"]:
        g = ours_by.get(want["roadway_id"])
        if g is None:
            raise ValueError(f"请求里的巷道 {want['roadway_id']} 不在 personnel 的受威胁人员里（记录不配套）")
        er = g["escape_route"]
        ours = {"reachable": bool(er.get("reachable"))}
        if ours["reachable"]:
            ours.update(target=er["target"], target_name=er["target_name"], roadways=er["roadways"], length_m=er["length_m"])
        svc = service_side(g, by_group.get(g["roadway_id"]), contaminated, lengths, names)
        if routes.get("error") and svc["status"] == "unavailable" and not by_group:
            svc["error"] = routes["error"]
        cmp_ = compare(ours, svc)
        groups.append({"roadway_id": g["roadway_id"], "count": g["count"], "ours": ours, "service": svc, "comparison": cmp_, "text": text_line(g, ours, svc, cmp_)})
    counts: dict[str, int] = {}
    for g in groups:
        counts[g["comparison"]["case"]] = counts.get(g["comparison"]["case"], 0) + 1
    unavailable = counts.get("service_unavailable", 0)
    status = "unavailable" if unavailable == len(groups) else ("partial" if unavailable else "ok")
    out = {**base, "computed": True, "status": status, "note": None, "groups": groups, "counts": dict(sorted(counts.items())), "lines": [g["text"] for g in groups]}
    return out, {}, ["外部服务的结果是试验性的第二意见（输入含占位值），不替代我方路线；两者不一致时都显示，由人判断"]


if __name__ == "__main__":
    main(VERSION, run)
