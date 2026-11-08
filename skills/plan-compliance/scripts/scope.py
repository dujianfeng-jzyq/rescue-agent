"""合规审核的范围：按方案步骤和方案依据给出要对照的条文（条号），以及审核项清单。

输入：{"plan": <sealing-plan 的 compose.py 输出>}。条文原文由固定流程从知识库的条文索引取（不由脚本、不由模型写）；
“合规审核”阶段只能引用这里给出的条号。
"""

from _skill import load_reference, main, record_result

VERSION = "scope/1.0.0"


def _num(article):
    return int(article.strip("第条"))


def run(data, _args):
    p = record_result(data, "plan")
    plan = p["plan"]
    cfg = load_reference("review.yaml")
    doc = cfg["document"]
    by_kind = (cfg.get("kinds") or {}).get(plan.get("kind"), {})
    always, items = by_kind.get("always", cfg["always"]), by_kind.get("items", cfg["items"])
    wanted = set(always)
    for s in plan["steps"]:
        wanted.update(cfg["step_articles"].get(s["type"], []))
    wanted.update(b["article"] for b in plan["basis"] if b["document"] == doc and b.get("article"))
    articles = [{"document": doc, "article": a} for a in sorted(wanted, key=_num)]
    result = {
        "panel": p["panel"],
        "as_of": p["as_of"],
        "plan_id": plan["plan_id"],
        "articles": articles,
        "items": items,
        "conclusions": [{"id": k, "label": v} for k, v in cfg["conclusions"].items()],
        "limits": cfg["limits"],
    }
    params = {k: cfg[k] for k in ("document", "step_articles", "always")}
    if cfg.get("kinds"):
        params["kinds"] = cfg["kinds"]
    return result, params, []


if __name__ == "__main__":
    main(VERSION, run)
