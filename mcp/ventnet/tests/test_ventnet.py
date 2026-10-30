import asyncio

from mcp.client import Client
from rescue_mine_data_sim.mine import NETWORK
from rescue_mine_data_sim.server import SimHolder
from rescue_mine_data_sim.server import build_server as build_mine
from rescue_ventnet import network
from rescue_ventnet.server import build_server


def test_downstream_of_face_follows_return_airways():
    ids = [e.roadway_id for e in network.downstream(NETWORK, "R-1201-FACE")]
    assert ids[0] == "R-1201-FACE"
    assert {"R-1201-RET", "R-RET-MAIN", "R-RET-SHAFT"} <= set(ids)
    assert "R-1201-IN" not in ids  # 进风侧不受影响
    times = [e.arrival_min for e in network.downstream(NETWORK, "R-1201-FACE")]
    assert times == sorted(times)


def test_escape_route_avoids_contaminated_roadways():
    contaminated = {e.roadway_id for e in network.downstream(NETWORK, "R-1201-FACE")}
    route = network.escape_route(NETWORK, "R-1201-FACE", contaminated)
    assert route["reachable"]
    assert not (set(route["roadways"]) & (contaminated - {"R-1201-FACE"}))


def test_threatened_personnel_via_server():
    holder = SimHolder("s2-slow-oxidation", manual_clock=True)
    holder.engine.clock.advance(10)  # 白班
    server = build_server(build_mine(holder))

    async def go():
        async with Client(server) as c:
            r = await c.call_tool("threatened_personnel", {"source_roadway_id": "R-1201-FACE"})
            return r.structured_content

    result = asyncio.run(go())
    assert result["total_threatened"] >= 9  # 工作面 9 人 + 回风巷人员
    faces = [g for g in result["threatened"] if g["roadway_id"] == "R-1201-FACE"]
    assert faces and faces[0]["escape_route"]["reachable"]
    # 人数都由代码给出：各组 count、受威胁 + 不受威胁 = 井下合计
    assert all(g["count"] == len(g["persons"]) for g in result["threatened"] + result["not_threatened"])
    assert result["total_threatened"] + result["total_not_threatened"] == result["total_underground"] > result["total_threatened"]
