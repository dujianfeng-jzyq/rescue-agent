"""外部路径规划服务的编号约定：route_request.py 生成、route_compare.py 换回（docs/design-v6-route-service.md 附录 A.2）。

每条巷道拆成两段：起点 → 中间节点 → 终点。中间节点编号 `<巷道编号>#mid`，两段编号 `<巷道编号>#a`、`<巷道编号>#b`。
受威胁人员所在巷道的中间节点就是服务请求的起点。
"""

from __future__ import annotations

MID = "#mid"
SEGMENTS = ("#a", "#b")


def mid_node(roadway_id: str) -> str:
    return roadway_id + MID


def plain_nodes(node_ids: list[str]) -> list[str]:
    """节点序列去掉中间节点（只留我方网络的节点）。"""
    return [n for n in node_ids if not n.endswith(MID)]


def roadway_of(segment_id: str) -> str:
    """分段编号 → 巷道编号（不是分段编号时原样返回）。"""
    for suffix in SEGMENTS:
        if segment_id.endswith(suffix):
            return segment_id[: -len(suffix)]
    return segment_id


def roadways_of(segment_ids: list[str]) -> list[str]:
    """分段序列 → 巷道序列（相邻重复的合并）。"""
    out: list[str] = []
    for s in segment_ids:
        r = roadway_of(s)
        if not out or out[-1] != r:
            out.append(r)
    return out
