#!/usr/bin/env python3
"""离线入库第 2 步：把 var/kb-raw/ 里缓存的切片按条切分，生成条文索引（docs/design-v2.md 4.7）。

    uv run --project mcp python scripts/kb/build_article_index.py              # 生成 dsh-bundle/data/article-index.json
    uv run --project mcp python scripts/kb/build_article_index.py --show 矿山救援规程 85 164   # 抽查：打印这几条

只处理按“第X条”组织的文档（法律、条例、规程、细则、办法、规定）；标准和其他文档只走检索，列在 skipped 里。
条号不连续或缺号的文档仍然写入，但问题写进 documents[].problems，供抽查。切分规则在 articles.py，有测试。
索引版本是内容的 sha256 前 12 位：内容不变时版本不变。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import articles as A  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "var" / "kb-raw"
OUT = ROOT / "dsh-bundle" / "data" / "article-index.json"
BUILDER = "article-index/1.0.0"


def build() -> dict:
    documents, articles, skipped = [], {}, []
    for f in sorted(RAW.glob("*.json")):
        raw = json.loads(f.read_text(encoding="utf-8"))
        name = A.document_name(raw["name"])
        if not A.is_article_document(raw["chunks"]):
            skipped.append({"document": name, "reason": "不是按“第X条”组织的文档（标准、要点等），只走检索"})
            continue
        arts, problems = A.split_articles(raw["chunks"])
        entries = []
        for a in arts:
            d = a.to_dict()
            entries.append({
                "number": d["number"], "heading": d["heading"], "chapter": d["chapter"], "section": d["section"],
                "paragraphs": d["paragraphs"],
                "items": [{"label": x["label"], "number": x["number"], "paragraph": d["paragraphs"].index(x["text"])} for x in d["items"]],
                "chunk_ids": d["chunk_ids"],
            })  # fmt: skip
        articles[name] = entries
        documents.append({
            "document": name, "file_name": raw["name"], "dataset_id": raw["dataset_id"], "document_id": raw["document_id"],
            "source_update_time": raw.get("update_time"), "article_count": len(entries),
            "first": entries[0]["number"] if entries else None, "last": entries[-1]["number"] if entries else None,
            "problems": problems,
        })  # fmt: skip
    body = {"documents": documents, "articles": articles, "skipped": skipped}
    version = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
    return {"version": version, "builder": BUILDER, "built_at": datetime.now(UTC).isoformat(timespec="seconds"), **body}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", nargs="+", metavar=("文档", "条号"), help="抽查：打印某文档的若干条")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    if not any(RAW.glob("*.json")):
        print(f"{RAW} 里没有缓存的切片，先运行 scripts/kb/fetch_chunks.py", file=sys.stderr)
        return 2
    index = build()
    if args.show:
        doc, nums = args.show[0], [int(x) for x in args.show[1:]]
        by = {a["number"]: a for a in index["articles"].get(doc, [])}
        for n in nums:
            a = by.get(n)
            print(f"== {doc} 第{n}条 {'' if a else '（索引里没有）'}")
            if a:
                print(f"   [{a['chapter']} / {a['section']}]  切片 {len(a['chunk_ids'])} 个")
                print("\n".join(a["paragraphs"]))
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for d in index["documents"]:
        flag = "；问题：" + "、".join(d["problems"]) if d["problems"] else ""
        print(f"{d['document']}：{d['article_count']} 条（第{d['first']}–{d['last']}条）{flag}")
    for s in index["skipped"]:
        print(f"跳过 {s['document']}：{s['reason']}")
    print(f"索引版本 {index['version']}，写入 {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
