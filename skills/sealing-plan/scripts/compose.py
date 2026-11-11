"""按“方案编写”阶段的选择组装封闭方案，计算物资用量并与库存比较。

输入：{"skeleton": <skeleton.py 的输出>, "evidence": <同一份证据（物资台账、通风网络）>,
       "options": {"selection": {"optional_steps": [<可选步骤 key>], "seal_roadways": [<巷道编号>],
                                 "step_notes": [{"key": <步骤 key>, "note": <补充说明>}], "summary": <方案概述>}}}。

选择只能来自骨架给出的清单（不在清单里就报输入错误）。必备步骤的类型、相对顺序和说明由骨架决定；可选步骤按 after
插在指定步骤之后；补充说明接在固定说明之后。现场条件原样取自骨架，不能改。
材料用量：临时、永久密闭道数 = 所选密闭巷道数；选了注浆时按采空区和工作面长度估算注浆量；有注氮流量时与制氮产能比较。
数量都在这里算好（含步骤数、缺口数），照抄，不要自己数。
"""

from _materials import estimate_inerting, grout_volume, seal_materials, supply
from _skill import evidence, load_reference, main, record_result

VERSION = "compose/1.0.0"

STEP_NAMES = {
    "monitor": "检测观测",
    "evacuate": "撤人",
    "power_cutoff": "切断电源",
    "inert_injection": "注惰",
    "grouting": "注浆",
    "inhibitor_spray": "喷洒阻化剂",
    "leak_sealing": "堵漏",
    "pressure_balancing": "均压",
    "temporary_seal": "临时密闭",
    "permanent_seal": "永久密闭",
}
ITEM_NAMES = {
    "temporary_seal_kit": "快速密闭材料（套）",
    "brick_pcs": "砖（块）",
    "cement_t": "水泥（t）",
    "sand_m3": "砂（m³）",
    "grout_material_t": "注浆干料（t）",
    "nitrogen_capacity_m3_h": "制氮产能（m³/h）",
}


def _unique(values, what):
    if len(set(values)) != len(values):
        raise ValueError(f"{what}有重复")
    return values


def select(sk, sel):
    """校验选择，返回（步骤列表（带 key）, 密闭巷道, 概述）。"""
    if not isinstance(sel, dict):
        raise ValueError("缺少 options.selection（“方案编写”阶段的选择）")
    optional = {s["key"]: s for s in sk["optional_steps"]}
    chosen = _unique([str(k) for k in sel.get("optional_steps") or []], "可选步骤")
    if bad := [k for k in chosen if k not in optional]:
        raise ValueError(f"可选步骤不在清单里：{'、'.join(bad)}")
    seal_ids = {o["roadway_id"] for o in sk["seal_roadway_options"]}
    seals = _unique([str(r) for r in sel.get("seal_roadways") or []], "密闭巷道")
    if not seals:
        raise ValueError("至少选一条密闭巷道")
    if bad := [r for r in seals if r not in seal_ids]:
        raise ValueError(f"密闭巷道不在清单里：{'、'.join(bad)}")
    steps = []
    for s in sk["required_steps"]:
        steps.append(s)
        steps += [optional[k] for k in optional if k in chosen and optional[k]["after"] == s["key"]]
    keys = {s["key"] for s in steps}
    notes = {}
    for n in sel.get("step_notes") or []:
        k, text = str(n.get("key")), str(n.get("note") or "").strip()
        if k not in keys:
            raise ValueError(f"补充说明的步骤不在方案里：{k}")
        if k in notes:
            raise ValueError(f"步骤 {k} 的补充说明重复")
        if text:
            notes[k] = text
    return steps, seals, notes, str(sel.get("summary") or "").strip()


def run(data, _args):
    sk = record_result(data, "skeleton")
    ev = evidence(data)
    cfg = load_reference("materials.yaml")
    sel = (data.get("options") or {}).get("selection")
    steps, seals, notes, summary = select(sk, sel)
    ctx = sk["context"]
    warnings = []
    roads = {"threatened": sk["evacuate_roadways"], "seal": seals, "leak": sk["leak_roadways"]}

    inert = None
    leak, target = sk["inert_inputs"]["leak_m3_min"], sk["inert_inputs"]["target_o2_pct"]
    if leak is not None and target is not None and ctx["o2_pct"] is not None:
        inert = estimate_inerting(leak, ctx["o2_pct"], target, cfg["inert"]["penetration"])

    plan_steps, view = [], []
    for i, s in enumerate(steps, start=1):
        params = {}
        if s["type"] == "inert_injection":
            params["gas"] = "N2"
            if inert:
                params["rate_m3_h"] = inert["required_n2_m3_h"]
        desc = s["description"] + (f"。补充：{notes[s['key']]}" if s["key"] in notes else "")
        rw = list(roads.get(s.get("roadways"), []))
        plan_steps.append({"order": i, "type": s["type"], "description": desc, "roadways": rw, "team_size": None, "standby_team": None, "params": params})
        view.append({
            "order": i, "key": s["key"], "type": s["type"], "type_name": STEP_NAMES.get(s["type"], s["type"]), "description": desc,
            "roadways_text": "、".join(rw), "params_text": "；".join(f"{k}={v}" for k, v in params.items()),
        })  # fmt: skip

    # 物资用量
    materials, needs = {}, {}
    for kind, key in (("temporary", "temporary_seal"), ("permanent", "permanent_seal")):
        walls = sum(len(p["roadways"]) for p in plan_steps if p["type"] == key)
        if walls:
            r = seal_materials(walls, kind, cfg["seal"])
            materials[f"{kind}_seal"] = r
            for item, qty in r["materials"].items():
                if item != "mortar_m3":  # 砂浆现场拌制，按水泥、砂调运
                    needs[item] = needs.get(item, 0) + qty
    if any(p["type"] == "grouting" for p in plan_steps):
        net = ev["network"]["roadways"]
        face = next((r for r in net if r["kind"] == "working_face" and sk["panel"] in r["id"]), None)
        goaf = next((r for r in net if r["kind"] == "goaf" and sk["panel"] in r["id"]), None)
        if face is None or goaf is None:
            warnings.append(f"网络中找不到工作面 {sk['panel']} 的工作面巷道或采空区，不能估算注浆量")
        else:
            g = grout_volume(goaf["length_m"], face["length_m"], cfg["grout"])
            materials["grout"] = {**g, "strike_from": f"采空区漏风通道 {goaf['id']} 长度（数据中没有采空区走向长度）", "face_from": face["id"]}
            needs["grout_material_t"] = g["dry_material_t"]
    if inert:
        materials["inert"] = inert
    stock = ev.get("inventory")
    if stock is None:
        raise ValueError("证据里没有物资台账（采集清单 plan 应取 inventory）")
    rows = [supply(stock, item, qty) for item, qty in needs.items()]
    if inert:
        rows.append(supply(stock, "nitrogen_capacity_m3_h", float(inert["required_n2_m3_h"])))
    continuous = set(cfg.get("continuous_supply") or [])
    for r in rows:
        r["label"] = ITEM_NAMES.get(r["item_type"], r["item_type"])
        r["continuous"] = r["item_type"] in continuous  # 持续供应：总量照算，不算缺口
    shortages = [r for r in rows if not r["sufficient"] and not r["continuous"]]

    plan = {
        "plan_id": sk["plan_id"],
        "kind": sk["kind"],
        "title": sk["title"],
        "context": ctx,
        "steps": plan_steps,
        "basis": [{"document": b["document"], "article": b["article"], "source": "index", "note": None} for b in sk["basis"]],
        "panel": sk["panel"],
    }
    result = {
        "panel": sk["panel"],
        "as_of": sk["as_of"],
        "plan": plan,
        "summary": summary,
        "steps_view": view,
        "step_count": len(plan_steps),
        "optional_steps": [s["key"] for s in steps if s["key"] not in {r["key"] for r in sk["required_steps"]}],
        "seal_roadways": seals,
        "seal_walls_text": "、".join(seals),
        "materials": materials,
        "supply": rows,
        "shortages": shortages,
        "shortage_count": len(shortages),
        "has_shortages": bool(shortages),
        "missing_inputs": sk["missing_inputs"],
        "has_missing": bool(sk["missing_inputs"]),
        "human_inputs": sk["human_inputs"],
        "has_human_inputs": bool(sk["human_inputs"]),
        "explosion": sk["explosion"],
        "context_sources": sk["context_sources"],
    }
    params = {"materials": {k: v for k, v in cfg.items() if k not in ("status", "reviewed_by")}}
    return result, params, warnings


if __name__ == "__main__":
    main(VERSION, run)
