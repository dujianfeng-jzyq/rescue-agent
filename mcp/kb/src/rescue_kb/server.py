"""kb 服务：知识检索封装层 + 红线自查。智能体只调用本服务，不直接连接 RAGFlow。

封装层负责：工具形式、知识库映射、检索参数、查询改写、统一引用格式、缓存与降级、检索日志。
RAGFlow 未配置或不可用时，法规检索和按条取原文退回本地留存条款（标记 source=local-fallback）。

check_plan_redlines 是只读自查工具，供处置方案 Agent 和合规审核 Agent 在提交前使用；
它与 actions 服务的强制校验共用 rescue_contract.validation，结论一致（自查不检查数据时效）。
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from mcp.server.mcpserver import MCPServer
from rescue_contract.models import Plan
from rescue_contract.redlines import RULES
from rescue_contract.validation import precheck

from .ragflow import RagflowConfig, RagflowError, retrieve

log = logging.getLogger("rescue.kb")
LOCAL_ARTICLES = yaml.safe_load(Path(__file__).with_name("local_articles.yaml").read_text(encoding="utf-8"))
ARTICLE_RE = re.compile(r"第[0-9一二三四五六七八九十百零]+条")

# 查询改写：补充行业同义词。TODO(专家): 持续补充
SYNONYMS = {
    "上隅角": ["回风隅角"],
    "束管": ["束管监测", "气体分析"],
    "注氮": ["注惰性气体", "氮气防灭火"],
    "自燃": ["自然发火", "煤自燃"],
    "封闭": ["密闭", "隔绝灭火"],
    "启封": ["打开密闭", "火区启封"],
}

INSTRUCTIONS = (
    "知识检索服务：法规规程、矿井预案、文献案例。每条结果都带文档名和条号（如有），"
    "方案中引用依据时必须照抄 document 和 article 字段。source 为 local-fallback 表示知识库暂不可用、"
    "使用本地留存条款，需在结论中注明；本地留存了多少条、哪几条照抄 local_coverage，不要自己数。"
)


def _datasets(kind: str) -> list[str]:
    raw = os.environ.get(f"KB_DATASETS_{kind.upper()}", "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def rewrite(query: str) -> str:
    extra = [w for k, ws in SYNONYMS.items() if k in query for w in ws if w not in query]
    return query if not extra else f"{query} {' '.join(extra)}"


def _local_search(query: str, limit: int) -> list[dict[str, Any]]:
    """关键词打分：原始查询词权重 3，改写补充的同义词权重 1，命中标签额外加分。"""
    original = [t for t in re.split(r"\s+", query) if t]
    expanded = [t for t in re.split(r"\s+", rewrite(query)) if t and t not in original]
    scored = []
    for a in LOCAL_ARTICLES:
        text = a["text"] + " ".join(a["tags"]) + a["article"]
        score = (
            3 * sum(text.count(t) for t in original) + sum(text.count(t) for t in expanded) + sum(2 for tag in a["tags"] if tag in query)
        )
        if score:
            scored.append((score, a))
    scored.sort(key=lambda x: -x[0])
    return [
        {"document": a["document"], "article": a["article"], "content": a["text"], "similarity": None, "source": "local-fallback"}
        for _, a in scored[:limit]
    ]


def _format(chunks) -> list[dict[str, Any]]:
    out, seen = [], set()
    for c in chunks:
        key = (c.document, c.content[:80])
        if key in seen:
            continue
        seen.add(key)
        m = ARTICLE_RE.search(c.content)
        out.append(
            {
                "document": c.document,
                "article": m.group(0) if m else None,
                "content": c.content,
                "similarity": round(c.similarity, 3) if c.similarity is not None else None,
                "source": "ragflow",
            }
        )
    return out


class KnowledgeBase:
    def __init__(self, cfg: RagflowConfig | None = None) -> None:
        self.cfg = cfg if cfg is not None else RagflowConfig.from_env()
        self._cached = lru_cache(maxsize=256)(self._search_uncached)

    def _search_uncached(self, kind: str, query: str, limit: int) -> tuple[dict[str, Any], ...]:
        datasets = _datasets(kind)
        q = rewrite(query)
        if self.cfg and datasets:
            try:
                res = _format(retrieve(self.cfg, q, datasets, page_size=limit))
                log.info("kb.search kind=%s query=%r hits=%d", kind, query, len(res))
                return tuple(res)
            except RagflowError as exc:
                log.warning("kb.search 降级到本地：%s", exc)
        if kind == "regulations":
            return tuple(_local_search(query, limit))
        return ()

    def search(self, kind: str, query: str, limit: int = 6) -> dict[str, Any]:
        results = list(self._cached(kind, query, limit))
        status = "ok" if self.cfg and _datasets(kind) else "not-configured"
        out: dict[str, Any] = {"query": query, "status": status, "results": results}
        if results and all(r["source"] == "local-fallback" for r in results):
            out["status"] = "local-fallback"
            # 说明降级时能引用哪些条款，由代码给出，Agent 不用自己数
            out["local_coverage"] = {"count": len(LOCAL_ARTICLES), "articles": [f"{a['document']} {a['article']}" for a in LOCAL_ARTICLES]}
        return out


def build_server(kb: KnowledgeBase | None = None) -> MCPServer:
    kb = kb or KnowledgeBase()
    server = MCPServer("kb", instructions=INSTRUCTIONS)

    @server.tool()
    def search_regulations(query: str, limit: int = 6) -> dict[str, Any]:
        """检索法规规程（《矿山救援规程》《煤矿安全规程》《煤矿防灭火细则》等）。"""
        return kb.search("regulations", query, limit)

    @server.tool()
    def search_mine_plans(query: str, limit: int = 6) -> dict[str, Any]:
        """检索本矿应急预案、火灾专项预案、防灭火设计和现场处置方案。"""
        return kb.search("plans", query, limit)

    @server.tool()
    def search_cases(query: str, limit: int = 6) -> dict[str, Any]:
        """检索事故调查报告和研究文献中的案例。"""
        return kb.search("cases", query, limit)

    @server.tool()
    def get_article(document: str, article: str) -> dict[str, Any]:
        """按文档名和条号取条款原文，例如 document="矿山救援规程", article="第85条"。
        红线相关条款优先使用本地留存版本，保证知识库不可用时也能引用。"""
        for a in LOCAL_ARTICLES:
            if a["document"] == document and a["article"] == article:
                return {"document": document, "article": article, "content": a["text"], "source": "local-fallback"}
        hits = kb.search("regulations", f"{document} {article}", 10)["results"]
        for h in hits:
            if h.get("article") == article and document in h["document"]:
                return h
        return {"document": document, "article": article, "content": None, "source": "not-found"}

    @server.tool()
    def check_plan_redlines(tool: str, plan: Plan) -> dict[str, Any]:
        """对结构化方案做红线自查（只读，不发布）。tool 取 issue_firefighting_plan / issue_sealing_plan / approve_unsealing。
        返回 ok 和问题清单（含条号）。注意：自查不检查数据时效，发布时 actions 服务还会检查。"""
        result = precheck(tool, {"plan": plan.model_dump(mode="json")}, None)
        return {**result, "rules_checked": [f"{r.rule_id} {r.article} {r.summary}" for r in RULES]}

    return server
