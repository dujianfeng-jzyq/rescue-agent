"""route_planner 适配服务：用本地假服务（固定响应）测编号转换、校验、超时、不可用（真实服务的检查在 rescue_route_planner.check，不进 CI）。"""

import asyncio
import json
from typing import Any

import httpx2
import pytest
from mcp.client import Client
from rescue_route_planner.check import sample, to_plan_input
from rescue_route_planner.server import build_server
from rescue_route_planner.service import Config

URL = "http://route.test"
CFG = Config(url=URL, timeout_s=1.0)

# 小网络：A —e1— B —e2— C（出口），B —e3— D（硐室），A —e4— D；e5 与 e2 并行
NODES = [{"id": i, "xyz": [x, 0, 0]} for i, x in (("A", 0), ("B", 100), ("C", 200), ("D", 300))]


def edge(eid, s, t, smoke=0.0, wind=1.0):
    return {"id": eid, "source": s, "target": t, "height": 3, "width": 4, "wind_speed": wind, "smoke": smoke}


EDGES = [edge("e1", "A", "B"), edge("e2", "B", "C"), edge("e3", "B", "D"), edge("e4", "A", "D", smoke=50)]
ARGS = {"nodes": NODES, "edges": EDGES, "start": "A", "targets": ["C", "D"], "walk_speed": 1.0, "max_smoke_time": 900}
IDX = {"A": 0, "B": 1, "C": 2, "D": 3}


def route(path, dist, smoke=0.0, escape=0.0):
    return {"path": [IDX[n] for n in path], "effectiveDistance": dist, "smokeTime": smoke, "temporaryEscapeTime": escape}


def fake(responses: dict[int, Any], seen: list | None = None) -> httpx2.MockTransport:
    """按请求的 target 下标返回固定响应：(状态码, 体) 或异常。"""

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append((request, body))
        r = responses[body["target"]]
        if isinstance(r, Exception):
            raise r
        status, payload = r
        return httpx2.Response(status, json=payload) if not isinstance(payload, str) else httpx2.Response(status, text=payload)

    return httpx2.MockTransport(handler)


def call(transport, args=ARGS, cfg=CFG, tool="plan_escape_routes"):
    async def go():
        async with Client(build_server(cfg, transport)) as c:
            return await c.call_tool(tool, args if tool == "plan_escape_routes" else {})

    return asyncio.run(go())


def ok(transport, **kw) -> dict[str, Any]:
    res = call(transport, **kw)
    assert not res.is_error, res.content
    return res.structured_content


def test_converts_ids_and_picks_best_by_provider_order():
    seen: list = []
    t = fake({
        IDX["C"]: (200, {"routes": [route("ABC", 250.0)]}),
        IDX["D"]: (200, {"routes": [route("AD", 200.0, smoke=100.0, escape=150.0)]}),
    }, seen)  # fmt: skip
    out = ok(t)
    assert out["available"] and out["error"] is None
    # 暂时脱险时间优先：无烟的 C（0）胜过更短但穿烟的 D（150）
    assert out["best"]["target"] == "C" and out["best"]["nodes"] == ["A", "B", "C"] and out["best"]["edges"] == ["e1", "e2"]
    assert {t["target"]: t["status"] for t in out["targets"]} == {"C": "ok", "D": "ok"}
    d = next(t for t in out["targets"] if t["target"] == "D")
    assert d["routes"][0]["edges"] == ["e4"] and d["routes"][0]["smoke_time_s"] == 100.0
    # 请求：下标形式、风速和烟雾字段名按提供方接口，target 逐个补上
    req = {b["target"]: b for _, b in seen}
    assert set(req) == {IDX["C"], IDX["D"]}
    b = req[IDX["C"]]
    assert b["start"] == 0 and b["nodes"][1] == [100.0, 0.0, 0.0]
    assert b["edges"][3] == {"source": 0, "target": 3, "height": 3.0, "width": 4.0, "windSpeed": 1.0, "smokeConcentration": 50.0}
    assert b["walkSpeed"] == 1.0 and b["maxSmokeTime"] == 900.0
    assert out["provider_request"] == {k: v for k, v in b.items() if k != "target"}


def test_tie_on_time_breaks_by_distance():
    t = fake({IDX["C"]: (200, {"routes": [route("ABC", 250.0)]}), IDX["D"]: (200, {"routes": [route("ABD", 240.0)]})})
    assert ok(t)["best"]["target"] == "D"


def test_empty_routes_is_answered_not_best():
    t = fake({IDX["C"]: (200, {"routes": []}), IDX["D"]: (200, {"routes": []})})
    out = ok(t)
    assert out["available"] and out["best"] is None
    assert [x["status"] for x in out["targets"]] == ["empty", "empty"]
    assert all(x["empty_reason"] is None for x in out["targets"])


def test_reason_and_version_passed_through_when_provider_adds_them():
    t = fake({IDX["C"]: (200, {"routes": [], "reason": "smoke_time_exceeded", "version": "v2"}), IDX["D"]: (200, {"routes": [], "version": "v2"})})
    out = ok(t)
    assert out["targets"][0]["empty_reason"] == "smoke_time_exceeded" and out["service_version"] == "v2"


def test_service_down_is_not_an_error():
    t = fake({IDX["C"]: httpx2.ConnectError("refused"), IDX["D"]: httpx2.ConnectError("refused")})
    out = ok(t)
    assert out["available"] is False and "连接失败" in out["error"] and out["best"] is None


def test_timeout_reported():
    t = fake({IDX["C"]: httpx2.ReadTimeout("slow"), IDX["D"]: (200, {"routes": [route("ABD", 240.0)]})})
    out = ok(t)
    assert out["available"] and out["best"]["target"] == "D"
    assert "部分终点调用失败" in out["error"] and "超时" in out["error"]


def test_provider_400_carries_message():
    t = fake({i: (400, {"error": "风速超出系数表范围"}) for i in (IDX["C"], IDX["D"])})
    out = ok(t)
    assert out["available"] is False and out["error"] == "服务返回 400：风速超出系数表范围"
    assert out["targets"][0]["raw_response"] == {"error": "风速超出系数表范围"}


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        (route("BC", 100.0), "首尾"),
        (route("ACD", 100.0) | {"path": [0, 2]}, "没有对应的巷道"),
        ({"path": [0, 9], "effectiveDistance": 1, "smokeTime": 0, "temporaryEscapeTime": 0}, "越界"),
        (route("ABC", -1.0), "非负"),
        ({"path": "ABC"}, "整数下标"),
    ],
)
def test_invalid_route_is_rejected(bad, why):
    t = fake({IDX["C"]: (200, {"routes": [bad]}), IDX["D"]: (200, {"routes": []})})
    out = ok(t)
    c = out["targets"][0]
    assert c["status"] == "error" and why in c["error"] and c["routes"] == []
    assert out["best"] is None


def test_non_json_body():
    t = fake({IDX["C"]: (500, "Internal Server Error"), IDX["D"]: (500, "Internal Server Error")})
    out = ok(t)
    assert out["available"] is False and out["error"] == "服务返回 500"


def test_parallel_edges_flagged():
    edges = [*EDGES, edge("e5", "C", "B")]
    t = fake({IDX["C"]: (200, {"routes": [route("ABC", 250.0)]}), IDX["D"]: (200, {"routes": []})})
    out = ok(t, args={**ARGS, "edges": edges})
    assert out["best"]["edges"] == ["e1", "e2"] and out["best"]["ambiguous_hops"] == [1]


@pytest.mark.parametrize(
    ("patch", "why"),
    [
        ({"edges": [*EDGES, edge("e9", "A", "Z")]}, "未知节点 Z"),
        ({"edges": [*EDGES, edge("e1", "C", "D")]}, "巷道编号重复"),
        ({"edges": [edge("e1", "A", "A")]}, "起点和终点相同"),
        ({"edges": [edge("e1", "A", "B", wind=16)]}, "风速 16.0 超出"),
        ({"edges": [edge("e1", "A", "B", smoke=101)]}, "烟雾浓度"),
        ({"start": "Q"}, "起点 Q"),
        ({"targets": []}, "没有终点"),
        ({"targets": ["C", "C"]}, "终点重复"),
        ({"targets": ["A"]}, "起点不能同时是终点"),
        ({"walk_speed": 0}, "步行速度"),
        ({"nodes": [*NODES, {"id": "A", "xyz": [0, 0, 0]}]}, "节点编号重复"),
        ({"nodes": [{"id": "A", "xyz": [0, 0]}, *NODES[1:]]}, "3 个有限数值"),
    ],
)
def test_input_errors_raise_tool_error(patch, why):
    res = call(fake({}), args={**ARGS, **patch})
    assert res.is_error
    assert why in res.content[0].text


def test_not_configured():
    out = ok(fake({}), cfg=Config(url=None))
    assert out["available"] is False and "未配置" in out["error"]


def test_status_tool():
    t = httpx2.MockTransport(lambda r: httpx2.Response(200, text="ok") if r.url.path == "/healthz" else httpx2.Response(404))
    res = call(t, tool="route_service_status")
    assert res.structured_content == {"ok": True, "service_url": URL, "detail": "200 ok"}


def test_token_header_sent():
    seen: list = []
    t = fake({IDX["C"]: (200, {"routes": []}), IDX["D"]: (200, {"routes": []})}, seen)
    ok(t, cfg=Config(url=URL, token="s3cret"))
    assert all(r.headers["authorization"] == "Bearer s3cret" for r, _ in seen)


def test_provider_sample_round_trip_offline():
    """提供方示例 input.json 换成我方编号后再换回，请求与原样一致（一致性检查的离线部分）。"""
    raw = sample()
    seen: list = []
    t = fake({raw["target"]: (200, {"routes": [{"path": [3, 6, 5, 4, 8, 7, 9], "effectiveDistance": 1465.49, "smokeTime": 236.19, "temporaryEscapeTime": 600.84}]})}, seen)
    inp = to_plan_input(raw)
    out = ok(t, args=inp.model_dump(mode="json"))
    assert seen[0][1] == raw
    assert out["best"]["edges"] == ["E4", "E3", "E2", "E6", "E5", "E7"]
