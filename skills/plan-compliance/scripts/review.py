"""方案合规审核的结论：核对“合规审核”阶段的选择，结合 actions 的红线预检，给出审核结论。

输入：{"plan": <compose.py 输出>, "scope": <scope.py 输出>,
       "options": {"redline": {"ok", "problems", "notes"}（actions 的 precheck_plan，原样）,
                   "review": {"items": [{"id", "conclusion", "articles": [条号], "note"}], "summary"}（阶段的选择）, "round": 轮次}}。

结论由代码定：红线预检不通过，或任一审核项为“不通过”，结论为不通过（fail）；否则通过（pass）。
“需人工决策”的审核项和缺失的人工确认项列为 human_items，下发时在人工确认弹窗里逐项列出。
审核项、结论、条号只能来自 scope 给出的清单（不在清单里就报输入错误）。输出里带上方案本身，actions 按本记录核对并下发。
"""

from _skill import main, record_result

VERSION = "review/1.0.0"


def run(data, _args):
    p = record_result(data, "plan")
    sc = record_result(data, "scope")
    opts = data.get("options") or {}
    red = opts.get("redline")
    rev = opts.get("review")
    if not isinstance(red, dict) or not isinstance(red.get("ok"), bool):
        raise ValueError("缺少 options.redline（actions 的红线预检结果）")
    if not isinstance(rev, dict) or not isinstance(rev.get("items"), list):
        raise ValueError("缺少 options.review（“合规审核”阶段的选择）")
    labels = {i["id"]: i["label"] for i in sc["items"]}
    names = {c["id"]: c["label"] for c in sc["conclusions"]}
    allowed = {a["article"] for a in sc["articles"]}
    items, seen = [], set()
    for x in rev["items"]:
        iid, concl = str(x.get("id")), str(x.get("conclusion"))
        if iid not in labels:
            raise ValueError(f"审核项不在清单里：{iid}")
        if iid in seen:
            raise ValueError(f"审核项重复：{iid}")
        seen.add(iid)
        if concl not in names:
            raise ValueError(f"审核结论只能是 {'、'.join(names)}，收到 {concl}")
        arts = [str(a) for a in x.get("articles") or []]
        if bad := [a for a in arts if a not in allowed]:
            raise ValueError(f"审核项 {iid} 引用了不在给定范围里的条文：{'、'.join(bad)}")
        items.append({"id": iid, "label": labels[iid], "conclusion": concl, "conclusion_name": names[concl],
                      "articles": arts, "articles_text": "、".join(arts), "note": str(x.get("note") or "").strip()})  # fmt: skip
    if missing := [i for i in labels if i not in seen]:
        raise ValueError(f"审核项没有逐项给出结论：{'、'.join(missing)}")
    failed = [i for i in items if i["conclusion"] == "fail"]
    needs_human = [i for i in items if i["conclusion"] == "needs_human"]
    verdict = "fail" if (not red["ok"] or failed) else "pass"
    human_items = [f"{m['label']}：{m['note']}" for m in p["missing_inputs"] if m.get("kind") == "human"]
    human_items += [f"{i['label']}：{i['note'] or '需人工决策'}" for i in needs_human]
    result = {
        "panel": p["panel"],
        "as_of": p["as_of"],
        "plan_id": p["plan"]["plan_id"],
        "round": opts.get("round"),
        "verdict": verdict,
        "verdict_name": "通过" if verdict == "pass" else "不通过",
        "redline": {"ok": red["ok"], "problems": list(red.get("problems") or []), "notes": list(red.get("notes") or [])},
        "items": items,
        "failed_items": failed,
        "failed_count": len(failed),
        "needs_human_count": len(needs_human),
        "human_items": human_items,
        "has_human_items": bool(human_items),
        "summary": str(rev.get("summary") or "").strip(),
        "plan": p["plan"],
    }
    return result, {"verdict_rule": "红线预检不通过或任一审核项不通过即为不通过"}, []


if __name__ == "__main__":
    main(VERSION, run)
