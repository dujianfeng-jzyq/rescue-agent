import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rescue_contract.redlines import RULES
from rescue_kb.ragflow import RagflowConfig
from rescue_kb.server import LOCAL_ARTICLES, KnowledgeBase, rewrite


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
