"""fire_sim 适配服务：用本地假服务（固定响应）测编号转换、校验、结果压缩、超时、不可用（真实服务的检查在 rescue_fire_sim.check，不进 CI）。"""

import asyncio
import json
from typing import Any

import httpx2
import pytest
from mcp.client import Client
from rescue_fire_sim.check import sample, to_sim_input
from rescue_fire_sim.server import build_server
from rescue_fire_sim.service import SIM_PATH, SPEC_PATH, Config

URL = "http://fire.test"
CFG = Config(url=URL, timeout_s=1.0)

# 小网络：入口 A —b1→ B —b2→ C 出口，B —b3→ C 并联
NODES = [{"id": i, "xyz": [x, 0, z]} for i, x, z in (("A", 0, 0), ("B", 100, -50), ("C", 200, 0))]
BRANCHES = [
    {"id": "b1", "from_node": "A", "to_node": "B", "resistance": 0.01, "area_m2": 12, "boundary": "inlet"},
    {"id": "b2", "from_node": "B", "to_node": "C", "resistance": 0.02, "area_m2": 12, "boundary": "outlet"},
    {"id": "b3", "from_node": "B", "to_node": "C", "resistance": 0.05, "area_m2": 10, "closed": True},
]
FIRE = {
    "branch": "b2",
    "position": 0.5,
    "total_time_s": 120,
    "output_interval_s": 60,
    "fuel_type": 0,
    "burning_area_m2": 10,
    "fuel_load_kg": 90,
    "oxygen_pct": 21,
}
BOUNDARY = {"mode": "forced", "inlet_flows_m3_s": {"A": 30.0}}
ARGS = {"nodes": NODES, "branches": BRANCHES, "fire": FIRE, "boundary": BOUNDARY}
TIMES = [0, 60, 120]


def provider_result(order=(0, 1, 2), speeds=None, co=None) -> dict[str, Any]:
    """提供方格式的返回：speeds[帧][巷道下标]、co[巷道下标] = 各帧的值（数字 0 或网格数组）。"""
    speeds = speeds or [[2.5, 1.5, 0.0], [2.5, 1.4, 0.0], [2.5, -0.2, 0.0]]
    co = co or {0: [0, 0, 0], 1: [0, [0.0, 0.003, 0.001], [0.002, 0.012, 0.004]], 2: [0, 0, 0]}
    return {
        "windSpeed": {"timeUnit": "s", "valueUnit": "m/s", "branchIds": list(order), "rows": [{"time": t, "values": [speeds[k][i] for i in order]} for k, t in enumerate(TIMES)]},
        "coConcentration": {
            "timeUnit": "s", "positionUnit": "m", "valueUnit": "vol%",
            "branches": [{"branchId": i, "positions": [0.0, 1.0, 2.0], "rows": [{"time": t, "values": co[i][k]} for k, t in enumerate(TIMES)]} for i in range(3)],
        },
    }  # fmt: skip


def fake(response: Any, seen: list | None = None) -> httpx2.MockTransport:
    """固定响应：(状态码, 体) 或异常；GET 接口说明另给。"""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET" and request.url.path == SPEC_PATH:
            return httpx2.Response(200, json={"info": {"version": "1.0"}, "paths": {SIM_PATH: {}}})
        if seen is not None:
            seen.append(json.loads(request.content))
        if isinstance(response, Exception):
            raise response
        status, payload = response
        return httpx2.Response(status, json=payload) if not isinstance(payload, str) else httpx2.Response(status, text=payload)

    return httpx2.MockTransport(handler)


def call(transport, args=ARGS, cfg=CFG, tool="simulate_fire"):
    async def go():
        async with Client(build_server(cfg, transport)) as c:
            return await c.call_tool(tool, args if tool == "simulate_fire" else {})

    return asyncio.run(go())


def ok(transport, **kw) -> dict[str, Any]:
    res = call(transport, **kw)
    assert not res.is_error, res.content
    return res.structured_content


def test_converts_ids_and_summarizes_per_branch():
    seen: list = []
    out = ok(fake((200, provider_result(order=(2, 0, 1))), seen))
    assert out["available"] and out["error"] is None and out["http_status"] == 200
    assert out["times_s"] == [0.0, 60.0, 120.0]
    by = {b["id"]: b for b in out["branches"]}
    # branchIds 打乱顺序也按下标对回
    assert by["b1"]["air_speed_m_s"] == [2.5, 2.5, 2.5] and by["b2"]["air_speed_m_s"] == [1.5, 1.4, -0.2]
    # CO：每帧全巷道最大值，vol% → ppm；全零压缩成数字 0
    assert by["b2"]["co_max_ppm"] == [0.0, 30.0, 120.0] and by["b2"]["co_first_s"] == 60.0 and by["b2"]["co_peak_ppm"] == 120.0
    assert by["b1"]["co_max_ppm"] == [0.0, 0.0, 0.0] and by["b1"]["co_first_s"] is None
    assert by["b2"]["air_reversed"] and not by["b1"]["air_reversed"]
    assert not by["b3"]["air_stopped"]  # 一开始就是 0（风门关闭）不算“停风”
    assert len(out["result_sha256"]) == 64 and out["result_bytes"] > 0
    # 请求：下标形式、字段名按提供方接口
    (req,) = seen
    assert req == out["provider_request"]
    assert req["fireSource"] == {
        "branchId": 1,
        "position": 0.5,
        "totalTime": 120,
        "outputInterval": 60,
        "fuelType": 0,
        "burningArea": 10,
        "fuelLoad": 90,
        "oxygenConcentration": 21,
    }
    assert req["ventBoundary"] == {"mode": 1, "inletFlows": {"0": 30.0}, "outletPressures": {}}
    assert [b["boundaryMarker"] for b in req["branches"]] == [1, 2, 0] and req["branches"][2]["closed"] is True
    assert req["branches"][1] == {
        "index": 1,
        "from": 1,
        "to": 2,
        "resistance": 0.02,
        "fixedFlow": 0.0,
        "closed": False,
        "area": 12,
        "boundaryMarker": 2,
    }
    assert req["nodes"][1] == {"index": 1, "x": 100, "y": 0, "z": -50}


def test_exhaust_mode_maps_to_2():
    seen: list = []
    ok(fake((200, provider_result()), seen), args={**ARGS, "boundary": {"mode": "exhaust", "outlet_pressures_pa": {"C": -1200.0}}})
    assert seen[0]["ventBoundary"] == {"mode": 2, "inletFlows": {}, "outletPressures": {"2": -1200.0}}


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        (lambda a: a["fire"].update(branch="nope"), "火源巷道 nope 不在 branches 里"),
        (lambda a: a["fire"].update(position=1.5), "火源位置应在 [0, 1]"),
        (lambda a: a["fire"].update(fuel_type=9), "燃料类型应为 0–5"),
        (lambda a: a["fire"].update(output_interval_s=1, total_time_s=36000), "输出帧数超过上限"),
        (lambda a: a["fire"].update(fuel_load_kg=0), "燃料质量应大于 0"),
        (lambda a: a["branches"][0].update(resistance=0), "巷道 b1 的风阻应大于 0"),
        (lambda a: a["branches"][1].update(to_node="Z"), "巷道 b2 引用了未知节点 Z"),
        (lambda a: a["branches"].append(dict(a["branches"][0])), "巷道编号重复：b1"),
        (lambda a: a["boundary"].update(inlet_flows_m3_s={}), "压入式要给进风节点风量"),
        (lambda a: a["boundary"].update(inlet_flows_m3_s={"Q": 1.0}), "通风边界引用了未知节点 Q"),
    ],
)
def test_bad_input_is_tool_error(patch, message):
    args = json.loads(json.dumps(ARGS))
    patch(args)
    seen: list = []
    res = call(fake((200, provider_result()), seen), args=args)
    assert res.is_error and message in res.content[0].text
    assert seen == []  # 不合法的输入不发给提供方


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ((400, {"error": "火源巷道 9 不存在"}), "服务返回 400：火源巷道 9 不存在"),
        ((500, {"error": "火风压计算失败: peakMassBurningRate must be finite and greater than zero"}), "服务返回 500：火风压计算失败"),
        ((400, {"title": "One or more validation errors occurred.", "errors": {"datas": ["required"]}}), "One or more validation errors"),
        ((200, "not json"), "返回格式不对"),
        ((200, {"windSpeed": {}}), "返回格式不对：缺少 windSpeed 或 coConcentration"),
        (httpx2.ConnectError("refused"), "连接失败：ConnectError"),
        (httpx2.ReadTimeout("slow"), "超时（1 秒）"),
    ],
)
def test_service_failures_are_unavailable_not_errors(response, error):
    out = ok(fake(response))
    assert out["available"] is False and error in out["error"]
    assert out["branches"] == [] and out["provider_request"]["fireSource"]["branchId"] == 1


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (provider_result(order=(0, 1)), "branchIds 与请求的巷道不一致"),
        ({**provider_result(), "windSpeed": {**provider_result()["windSpeed"], "valueUnit": "km/h"}}, "单位变了"),
        (provider_result(co={0: [0, 0, 0], 1: [0, [0.1, 0.2], 0], 2: [0, 0, 0]}), "巷道 b2 第 60 秒的 CO 网格数不对"),
        (provider_result(co={0: [0, 0, 0], 1: [0, [0.0, -0.1, 0.0], 0], 2: [0, 0, 0]}), "巷道 b2 第 60 秒的 CO 有非法数值"),
    ],
)
def test_malformed_results_are_rejected(body, error):
    out = ok(fake((200, body)))
    assert out["available"] is False and error in out["error"]


def test_not_configured():
    out = ok(fake((200, provider_result())), cfg=Config(url=None))
    assert out["available"] is False and "未配置" in out["error"]
    assert out["provider_request"]["branches"]  # 没配置也给出转换后的输入，便于核对


def test_status_reads_api_spec():
    st = call(fake((200, provider_result())), tool="fire_sim_status").structured_content
    assert st == {"ok": True, "service_url": URL, "detail": "接口说明版本 1.0"}
    st = call(fake((200, provider_result())), cfg=Config(url=None), tool="fire_sim_status").structured_content
    assert st["ok"] is False and "未配置" in st["detail"]


def test_provider_sample_round_trips():
    """提供方示例输入换成我方编号再经适配层转换，得到的请求与原样一致（只差注释已去掉）。"""
    from rescue_fire_sim.adapter import Indexed

    raw = sample()
    assert Indexed(to_sim_input(raw)).provider_request() == raw
