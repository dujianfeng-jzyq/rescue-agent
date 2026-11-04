#!/usr/bin/env python3
"""离线入库第 1 步：把 RAGFlow 法规数据集里各文档的全部切片取到本地缓存（var/kb-raw/<文档 id>.json）。

    uv run --project mcp python scripts/kb/fetch_chunks.py            # 全部法规文档（KB_DATASETS_REGULATIONS）
    uv run --project mcp python scripts/kb/fetch_chunks.py 矿山救援规程  # 文档名包含关键词的

读 .env 里的 RAGFLOW_BASE_URL、RAGFLOW_API_KEY。只读请求；网关断开时重试（rescue_kb.ragflow 的做法）。
第 2 步 build_article_index.py 只读这个缓存，不连 RAGFlow，可重复运行和测试。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var" / "kb-raw"


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            v = re.split(r"\s+#", v, maxsplit=1)[0]  # 行尾注释（与 shell 一样，# 前要有空白）
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get(path: str) -> dict:
    base, key = os.environ["RAGFLOW_BASE_URL"].rstrip("/"), os.environ["RAGFLOW_API_KEY"]
    req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {key}"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as f:
                data = json.loads(f.read())
            if data.get("code") not in (0, None):
                raise RuntimeError(data.get("message"))
            return data["data"]
        except (OSError, json.JSONDecodeError) as exc:
            if attempt == 4:
                raise
            print(f"  重试 {attempt + 1}：{exc}", file=sys.stderr)
            time.sleep(2**attempt)
    raise AssertionError


def main(argv: list[str]) -> int:
    load_env()
    OUT.mkdir(parents=True, exist_ok=True)
    for ds in [x.strip() for x in os.environ["KB_DATASETS_REGULATIONS"].split(",") if x.strip()]:
        docs = get(f"/api/v1/datasets/{ds}/documents?page=1&page_size=100")["docs"]
        for doc in docs:
            if argv and not any(k in doc["name"] for k in argv):
                continue
            chunks, page = [], 1
            while True:
                q = urllib.parse.urlencode({"page": page, "page_size": 100})
                part = get(f"/api/v1/datasets/{ds}/documents/{doc['id']}/chunks?{q}")
                chunks += part["chunks"]
                if len(chunks) >= part["total"] or not part["chunks"]:
                    break
                page += 1
            keep = [{k: c.get(k) for k in ("id", "content", "positions")} for c in chunks]
            out = {"dataset_id": ds, "document_id": doc["id"], "name": doc["name"], "update_time": doc.get("update_time"), "chunks": keep}
            (OUT / f"{doc['id']}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{doc['name']}：{len(keep)} 个切片")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
