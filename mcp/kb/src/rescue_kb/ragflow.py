"""RAGFlow HTTP 检索接口的最小客户端（POST /api/v1/retrieval）。

封装层直接调 HTTP 接口而不是 RAGFlow 自带的 MCP 服务，这样能用上完整的检索参数；
RAGFlow 升级导致接口变化时，只需改这个文件。
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
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
    retries: int = 3  # 连接被断开、超时时重试的次数（只对 GET 和检索，都是只读请求）

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
            retries=int(os.environ.get("KB_RETRIES", 3)),
        )


class RagflowError(RuntimeError):
    pass


def _request(cfg: RagflowConfig, method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{cfg.base_url}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
        method=method,
    )
    for attempt in range(cfg.retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
                payload = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:  # 服务端明确拒绝（4xx/5xx），重试没有意义
            raise RagflowError(f"RAGFlow 不可用：{exc}") from exc
        # 连接被断开（空响应）时 urllib 抛 RemoteDisconnected，不包装成 URLError，所以按 OSError 统一捕获
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
            if attempt == cfg.retries:
                raise RagflowError(f"RAGFlow 不可用：{exc}") from exc
            log.info("RAGFlow 请求失败，第 %d 次重试：%s", attempt + 1, exc)
            time.sleep(0.5 * 2**attempt)  # 连着断开时多等一会儿：0.5、1、2 秒
    if payload.get("code") not in (0, None):
        raise RagflowError(f"RAGFlow 返回错误：{payload.get('message')}")
    return payload


def find_documents(cfg: RagflowConfig, dataset_id: str, name: str, limit: int = 30) -> list[tuple[str, str]]:
    """按文档名关键词列出文档，返回 [(document_id, 文档名)]。"""
    q = urllib.parse.urlencode({"page": 1, "page_size": limit, "keywords": name})
    docs = (_request(cfg, "GET", f"/api/v1/datasets/{dataset_id}/documents?{q}").get("data") or {}).get("docs") or []
    return [(d["id"], d.get("name") or "") for d in docs]


def keyword_chunks(cfg: RagflowConfig, dataset_id: str, document_id: str, keywords: str, limit: int = 10) -> list[str]:
    """在一个文档里按关键词找切片，返回片段里确实含这个关键词的切片 id。
    带 keywords 时返回的 content 是去掉标点、加了 <em> 的高亮片段，不能当原文用；
    关键词检索是分词模糊匹配，“第九千九百九十九条”也会命中一堆切片，所以按片段过滤；
    高亮会把一个词拆成几段、中间插“...”（“<em>第八</em>...<em>十五条</em>”），过滤前去掉。
    拼错了也无妨：取回原文后还要按条文标题核对。"""
    q = urllib.parse.urlencode({"page": 1, "page_size": limit, "keywords": keywords})
    chunks = (_request(cfg, "GET", f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks?{q}").get("data") or {}).get("chunks") or []
    want = re.sub(r"\s+", "", keywords)
    return [c["id"] for c in chunks if c.get("id") and want in re.sub(r"<[^>]+>|\s+|\.\.\.|…", "", c.get("content") or "")]


def chunk_content(cfg: RagflowConfig, dataset_id: str, document_id: str, chunk_id: str) -> str | None:
    q = urllib.parse.urlencode({"id": chunk_id})
    chunks = (_request(cfg, "GET", f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks?{q}").get("data") or {}).get("chunks") or []
    return chunks[0].get("content") if chunks else None


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
    payload = _request(cfg, "POST", "/api/v1/retrieval", body)
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
