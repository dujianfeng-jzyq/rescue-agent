"""RAGFlow HTTP 检索接口的最小客户端（POST /api/v1/retrieval）。

封装层直接调 HTTP 接口而不是 RAGFlow 自带的 MCP 服务，这样能用上完整的检索参数；
RAGFlow 升级导致接口变化时，只需改这个文件。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("rescue.kb")


@dataclass(frozen=True)
class Chunk:
    document: str
    content: str
    similarity: float | None
    document_id: str | None
    dataset_id: str | None


@dataclass(frozen=True)
class RagflowConfig:
    base_url: str
    api_key: str
    similarity_threshold: float = 0.2
    page_size: int = 6
    timeout_s: float = 15

    @classmethod
    def from_env(cls) -> RagflowConfig | None:
        base, key = os.environ.get("RAGFLOW_BASE_URL"), os.environ.get("RAGFLOW_API_KEY")
        if not base or not key:
            return None
        return cls(
            base_url=base.rstrip("/"),
            api_key=key,
            similarity_threshold=float(os.environ.get("KB_SIMILARITY_THRESHOLD", 0.2)),
            page_size=int(os.environ.get("KB_PAGE_SIZE", 6)),
            timeout_s=float(os.environ.get("KB_TIMEOUT_S", 15)),
        )


class RagflowError(RuntimeError):
    pass


def retrieve(cfg: RagflowConfig, question: str, dataset_ids: list[str], page_size: int | None = None) -> list[Chunk]:
    body = {
        "question": question,
        "dataset_ids": dataset_ids,
        "page": 1,
        "page_size": page_size or cfg.page_size,
        "similarity_threshold": cfg.similarity_threshold,
        "keyword": True,
        "highlight": False,
    }
    req = urllib.request.Request(
        f"{cfg.base_url}/api/v1/retrieval",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RagflowError(f"RAGFlow 不可用：{exc}") from exc
    if payload.get("code") not in (0, None):
        raise RagflowError(f"RAGFlow 返回错误：{payload.get('message')}")
    chunks = (payload.get("data") or {}).get("chunks") or []
    return [
        Chunk(
            document=c.get("document_keyword") or c.get("docnm_kwd") or c.get("document_name") or "",
            content=c.get("content") or "",
            similarity=c.get("similarity"),
            document_id=c.get("document_id"),
            dataset_id=c.get("dataset_id") or c.get("kb_id"),
        )
        for c in chunks
    ]
