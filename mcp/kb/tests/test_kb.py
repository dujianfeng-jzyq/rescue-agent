import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rescue_contract.redlines import RULES
from rescue_kb.ragflow import RagflowConfig
from rescue_kb.server import LOCAL_ARTICLES, KnowledgeBase, _cn_article, article_number, build_server, find_article, rewrite


def test_every_redline_article_is_retained_locally():
    local = {a["article"] for a in LOCAL_ARTICLES}
    for rule in RULES:
        for art in rule.article.replace("第", "").replace("条", "").split("、"):
            assert f"第{art}条" in local, f"{rule.rule_id} 引用的 第{art}条 没有本地留存"


def test_query_rewrite_adds_synonyms():
    assert "自然发火" in rewrite("采空区自燃预警")


def test_not_configured_falls_back_to_local_articles(monkeypatch):
    monkeypatch.delenv("RAGFLOW_BASE_URL", raising=False)
    kb = KnowledgeBase(cfg=None)
    r = kb.search("regulations", "封闭 惰性气体")
    assert r["status"] == "local-fallback"
    assert r["results"][0]["article"] == "第85条"
    cov = r["local_coverage"]
    assert cov["count"] == len(LOCAL_ARTICLES) == len(cov["articles"]) and "矿山救援规程 第85条" in cov["articles"]


class _FakeRagflow(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["Authorization"] == "Bearer test-key"
        assert body["dataset_ids"] == ["ds-reg"]
        resp = {
            "code": 0,
            "data": {
                "chunks": [
                    {
                        "content": "第七十九条 用水灭火时，应当具备下列条件……",
                        "document_keyword": "矿山救援规程.pdf",
                        "similarity": 0.81,
                        "document_id": "d1",
                        "dataset_id": "ds-reg",
                    }
                ],
                "total": 1,
            },
        }
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_ragflow():
    srv = HTTPServer(("127.0.0.1", 0), _FakeRagflow)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_ragflow_results_are_normalised(fake_ragflow, monkeypatch):
    monkeypatch.setenv("KB_DATASETS_REGULATIONS", "ds-reg")
    kb = KnowledgeBase(cfg=RagflowConfig(base_url=fake_ragflow, api_key="test-key"))
    r = kb.search("regulations", "用水灭火条件")
    assert r["status"] == "ok"
    hit = r["results"][0]
    assert hit["document"] == "矿山救援规程.pdf" and hit["article"] == "第七十九条" and hit["source"] == "ragflow"


def test_ragflow_down_degrades_to_local(monkeypatch):
    monkeypatch.setenv("KB_DATASETS_REGULATIONS", "ds-reg")
    kb = KnowledgeBase(cfg=RagflowConfig(base_url="http://127.0.0.1:9", api_key="k", timeout_s=1))
    r = kb.search("regulations", "反风 撤人")
    assert r["status"] == "local-fallback" and r["results"][0]["article"] == "第77条"


class _EmptyReply(BaseHTTPRequestHandler):
    def do_POST(self):
        self.close_connection = True  # 不回任何字节就断开，等同 curl 的 Empty reply from server

    def log_message(self, *a):
        pass


def test_ragflow_empty_reply_degrades_to_local(monkeypatch):
    monkeypatch.setenv("KB_DATASETS_REGULATIONS", "ds-reg")
    srv = HTTPServer(("127.0.0.1", 0), _EmptyReply)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        kb = KnowledgeBase(cfg=RagflowConfig(base_url=f"http://127.0.0.1:{srv.server_address[1]}", api_key="k", timeout_s=2))
        r = kb.search("regulations", "反风 撤人")
    finally:
        srv.shutdown()
    assert r["status"] == "local-fallback" and r["results"][0]["article"] == "第77条"


def test_check_plan_redlines_tool_flags_missing_inerting():
    import asyncio
    from datetime import datetime

    from mcp.client import Client
    from rescue_kb.server import build_server

    server = build_server(KnowledgeBase(cfg=None))
    plan = {
        "plan_id": "P-1",
        "kind": "sealing",
        "title": "封闭",
        "context": {
            "fire_type": "goaf_spontaneous_combustion",
            "as_of": datetime(2026, 11, 2, 10).isoformat(),
            "explosion_risk": "unknown",
        },
        "steps": [{"order": 1, "type": "temporary_seal", "description": "临时密闭"}],
    }

    async def go():
        async with Client(server) as c:
            return await c.call_tool("check_plan_redlines", {"tool": "issue_sealing_plan", "plan": plan})

    r = asyncio.run(go())
    assert not r.is_error and r.structured_content["ok"] is False
    assert any("第85条" in p for p in r.structured_content["problems"])
    assert r.structured_content["rules_checked_count"] == len(RULES) == len(r.structured_content["rules_checked"])


@pytest.mark.parametrize(
    ("text", "n"),
    [("第85条", 85), ("第 85 条", 85), ("85", 85), ("第八十五条", 85), ("第十条", 10), ("第十五条", 15), ("第一百零一条", 101),
     ("第二百七十七条", 277), ("第七百六十六条", 766), ("第一千零一条", 1001), ("第条", None), ("第X条", None), ("", None)],
)
def test_article_number(text, n):
    assert article_number(text) == n


def test_cn_article_round_trip():
    for n in range(1, 1200):
        assert article_number(_cn_article(n)) == n, n
    assert _cn_article(10) == "第十条" and _cn_article(101) == "第一百零一条" and _cn_article(277) == "第二百七十七条"


def _hit(content, document="煤矿安全规程.pdf"):
    return {"document": document, "article": None, "content": content, "similarity": 0.5, "source": "ragflow"}


def test_find_article_matches_chinese_numerals_and_slices_chunk():
    hits = [_hit("第二百七十六条　火区封闭后……\n第二百七十七条　封闭的火区，只有经取样化验证实火已熄灭后，方可启封。\n第二百七十八条　启封……")]
    r = find_article(hits, "煤矿安全规程", "第277条")
    assert r["article"] == "第二百七十七条"
    assert r["content"].startswith("第二百七十七条") and "第二百七十八条" not in r["content"] and "第二百七十六条" not in r["content"]


def test_find_article_ignores_inline_references_and_other_documents():
    inline = [_hit("第十条　依照第二百七十七条规定启封火区。")]
    assert find_article(inline, "煤矿安全规程", "第277条") is None
    other = [_hit("第二百七十七条　……", document="金属非金属矿山安全规程.pdf")]
    assert find_article(other, "煤矿安全规程", "第277条") is None


class _ArticleRagflow(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _ArticleRagflow.questions.append(body["question"])
        chunk = {"content": "第二百七十六条　火区……\n第二百七十七条　封闭的火区，只有经取样化验证实火已熄灭后，方可启封。",
                 "document_keyword": "煤矿安全规程.pdf", "similarity": 0.6}
        data = json.dumps({"code": 0, "data": {"chunks": [chunk], "total": 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def _call(server, name, args):
    import asyncio

    from mcp.client import Client

    async def go():
        async with Client(server) as c:
            return await c.call_tool(name, args)

    r = asyncio.run(go())
    assert not r.is_error, r.content
    return r.structured_content


def test_get_article_tool_finds_ragflow_article_by_arabic_number(monkeypatch):
    monkeypatch.setenv("KB_DATASETS_REGULATIONS", "ds-reg")
    _ArticleRagflow.questions = []
    srv = HTTPServer(("127.0.0.1", 0), _ArticleRagflow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        server = build_server(KnowledgeBase(cfg=RagflowConfig(base_url=f"http://127.0.0.1:{srv.server_address[1]}", api_key="k")))
        out = _call(server, "get_article", {"document": "煤矿安全规程", "article": "第277条"})
    finally:
        srv.shutdown()
    assert out["source"] == "ragflow" and out["article"] == "第二百七十七条" and out["content"].startswith("第二百七十七条")
    assert "第二百七十七条" in _ArticleRagflow.questions[0]


def test_get_article_local_accepts_chinese_numerals(monkeypatch):
    monkeypatch.delenv("RAGFLOW_BASE_URL", raising=False)
    server = build_server(KnowledgeBase(cfg=None))
    out = _call(server, "get_article", {"document": "矿山救援规程", "article": "第八十五条"})
    assert out["source"] == "local-fallback" and out["article"] == "第85条"


class _ChunksRagflow(BaseHTTPRequestHandler):
    """模拟 RAGFlow 的文档列表和切片接口：带 keywords 时只返回去掉标点的高亮片段，按 id 取才是原文。"""

    FULL = {  # c10 排在前面：关键词也会命中它（正文引用了第二百七十七条），要靠标题判断跳过
        "c10": "第十条　依照第二百七十七条规定启封火区。",
        "c276": "第二百七十六条　火区封闭后……",
        "c277": "第二百七十七条　封闭的火区，只有经取样化验证实火已熄灭后，方可启封。",
    }

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        _ChunksRagflow.paths.append(u.path)
        if u.path == "/api/v1/datasets/ds-reg/documents":
            docs = [{"id": "d1", "name": "煤矿安全规程__abc.pdf"}, {"id": "d2", "name": "金属非金属矿山安全规程.pdf"}]
            data = {"docs": [d for d in docs if q.get("keywords", "") in d["name"]]}
        elif u.path == "/api/v1/datasets/ds-reg/documents/d1/chunks" and "id" in q:
            data = {"chunks": [{"id": q["id"], "content": self.FULL[q["id"]]}]}
        elif u.path == "/api/v1/datasets/ds-reg/documents/d1/chunks":
            kw = q["keywords"]
            ids = [cid for cid, text in self.FULL.items() if kw in text]
            data = {"chunks": [{"id": cid, "content": f"<em>{kw}</em>片段去掉了标点"} for cid in ids]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"code": 0, "data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_get_article_looks_up_document_chunks_directly(monkeypatch):
    monkeypatch.setenv("KB_DATASETS_REGULATIONS", "ds-reg")
    _ChunksRagflow.paths = []
    srv = HTTPServer(("127.0.0.1", 0), _ChunksRagflow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        server = build_server(KnowledgeBase(cfg=RagflowConfig(base_url=f"http://127.0.0.1:{srv.server_address[1]}", api_key="k")))
        out = _call(server, "get_article", {"document": "煤矿安全规程", "article": "第277条"})
    finally:
        srv.shutdown()
    # 按 id 取回原文（不是高亮片段），第十条里对第二百七十七条的引用不算
    assert out["source"] == "ragflow" and out["document"] == "煤矿安全规程__abc.pdf" and out["article"] == "第二百七十七条"
    assert out["content"] == _ChunksRagflow.FULL["c277"]
    assert "/api/v1/retrieval" not in _ChunksRagflow.paths


class _FlakyRagflow(_FakeRagflow):
    """第一次请求不回任何字节就断开，之后正常（模拟 RAGFlow 偶发的空响应）。"""

    calls = 0

    def do_POST(self):
        _FlakyRagflow.calls += 1
        if _FlakyRagflow.calls == 1:
            self.close_connection = True
            return
        super().do_POST()


def test_ragflow_retries_after_dropped_connection(monkeypatch):
    monkeypatch.setenv("KB_DATASETS_REGULATIONS", "ds-reg")
    _FlakyRagflow.calls = 0
    srv = HTTPServer(("127.0.0.1", 0), _FlakyRagflow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        kb = KnowledgeBase(cfg=RagflowConfig(base_url=f"http://127.0.0.1:{srv.server_address[1]}", api_key="test-key", timeout_s=2))
        r = kb.search("regulations", "用水灭火条件")
    finally:
        srv.shutdown()
    assert r["status"] == "ok" and r["results"][0]["source"] == "ragflow" and _FlakyRagflow.calls == 2
