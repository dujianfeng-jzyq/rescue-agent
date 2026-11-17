"""我方编号 ↔ 提供方服务（mine-route-api）的数组下标：输入校验、请求转换、返回路线校验和换回、最优终点选择。

纯函数，不访问网络（网络调用在 service.py）。提供方接口见 docs/design-v6-route-service.md 第 3、5 节：
- 输入 nodes 是 [x, y, z]（米），edges 用节点下标，`windSpeed` 正值表示风流从 source 流向 target；
- 一次调用只有一个终点 `target`；返回 `routes`（节点下标序列，routes[0] 最推荐），终点不可达和烟中时间超限都返回空列表；
- 排序规则：暂时脱险时间 → 烟中时间 → 当量距离，都是越小越好。我方选最优终点时沿用同一规则。
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from pydantic import BaseModel, Field

# 提供方接口说明（输入格式.json）给出的取值范围
WIND_SPEED_RANGE = (-15.0, 15.0)
SMOKE_RANGE = (0.0, 100.0)


class Node(BaseModel):
    id: str = Field(description="节点编号（我方编号）")
    xyz: list[float] = Field(description="[x, y, z]，单位 m")


class Edge(BaseModel):
    id: str = Field(description="巷道编号（我方编号；同一条巷道拆成几段时各段编号不同）")
    source: str = Field(description="起点节点编号")
    target: str = Field(description="终点节点编号")
    height: float = Field(description="巷道高度，m，> 0")
    width: float = Field(description="巷道宽度，m，> 0")
    wind_speed: float = Field(description="有符号风速，m/s，[-15, 15]；正值表示风流从 source 流向 target")
    smoke: float = Field(description="烟雾浓度，[0, 100]；0 表示无烟")


class PlanInput(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
    start: str
    targets: list[str]
    walk_speed: float
    max_smoke_time: float


class InputError(ValueError):
    """输入不合法（服务端转成 ToolError）。"""


def validate(inp: PlanInput) -> None:
    problems: list[str] = []
    ids = [n.id for n in inp.nodes]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        problems.append(f"节点编号重复：{'、'.join(dup)}")
    known = set(ids)
    for n in inp.nodes:
        if len(n.xyz) != 3 or not all(math.isfinite(v) for v in n.xyz):
            problems.append(f"节点 {n.id} 的坐标应为 3 个有限数值")
    eids = [e.id for e in inp.edges]
    dup = sorted({i for i in eids if eids.count(i) > 1})
    if dup:
        problems.append(f"巷道编号重复：{'、'.join(dup)}")
    for e in inp.edges:
        for end in (e.source, e.target):
            if end not in known:
                problems.append(f"巷道 {e.id} 引用了未知节点 {end}")
        if e.source == e.target:
            problems.append(f"巷道 {e.id} 的起点和终点相同")
        if not (e.height > 0 and e.width > 0):
            problems.append(f"巷道 {e.id} 的高度和宽度应大于 0")
        if not (WIND_SPEED_RANGE[0] <= e.wind_speed <= WIND_SPEED_RANGE[1]):
            problems.append(f"巷道 {e.id} 的风速 {e.wind_speed} 超出服务范围 {list(WIND_SPEED_RANGE)}")
        if not (SMOKE_RANGE[0] <= e.smoke <= SMOKE_RANGE[1]):
            problems.append(f"巷道 {e.id} 的烟雾浓度 {e.smoke} 超出服务范围 {list(SMOKE_RANGE)}")
    if inp.start not in known:
        problems.append(f"起点 {inp.start} 不是已知节点")
    if not inp.targets:
        problems.append("没有终点")
    for t in inp.targets:
        if t not in known:
            problems.append(f"终点 {t} 不是已知节点")
    if len(set(inp.targets)) != len(inp.targets):
        problems.append("终点重复")
    if inp.start in inp.targets:
        problems.append("起点不能同时是终点")
    if not inp.walk_speed > 0:
        problems.append("步行速度应大于 0")
    if not inp.max_smoke_time >= 0:
        problems.append("允许的最大烟中通行时间不能为负")
    if problems:
        raise InputError("；".join(problems))


class Indexed:
    """一次输入的编号 ↔ 下标对照。"""

    def __init__(self, inp: PlanInput) -> None:
        validate(inp)
        self.inp = inp
        self.node_ids = [n.id for n in inp.nodes]
        self.node_index = {nid: i for i, nid in enumerate(self.node_ids)}
        # 无序节点对 → 巷道编号（按输入顺序；并行巷道有多条）
        self.hops: dict[frozenset[int], list[str]] = {}
        for e in inp.edges:
            key = frozenset((self.node_index[e.source], self.node_index[e.target]))
            self.hops.setdefault(key, []).append(e.id)

    def provider_request(self) -> dict[str, Any]:
        """提供方输入（不含 target，逐终点调用时补上）。"""
        ni = self.node_index
        return {
            "nodes": [list(n.xyz) for n in self.inp.nodes],
            "edges": [
                {"source": ni[e.source], "target": ni[e.target], "height": e.height, "width": e.width, "windSpeed": e.wind_speed, "smokeConcentration": e.smoke}
                for e in self.inp.edges
            ],
            "start": ni[self.inp.start],
            "walkSpeed": self.inp.walk_speed,
            "maxSmokeTime": self.inp.max_smoke_time,
        }

    def convert_route(self, route: Any, target: str) -> dict[str, Any]:
        """把服务返回的一条路线换回我方编号；不合法抛 ValueError（说明原因）。"""
        if not isinstance(route, dict):
            raise ValueError("路线不是对象")
        path = route.get("path")
        if not isinstance(path, list) or len(path) < 2 or not all(isinstance(i, int) and not isinstance(i, bool) for i in path):
            raise ValueError("path 应为至少两个整数下标")
        if any(i < 0 or i >= len(self.node_ids) for i in path):
            raise ValueError("path 里有越界的节点下标")
        if path[0] != self.node_index[self.inp.start] or path[-1] != self.node_index[target]:
            raise ValueError("path 的首尾不是请求的起点和终点")
        edges: list[str] = []
        ambiguous: list[int] = []
        for k, (a, b) in enumerate(itertools.pairwise(path)):
            ids = self.hops.get(frozenset((a, b)))
            if not ids or a == b:
                raise ValueError(f"path 第 {k + 1} 步（{self.node_ids[a]} → {self.node_ids[b]}）没有对应的巷道")
            if len(ids) > 1:
                ambiguous.append(k)  # 并行巷道：服务只给节点，分不清走的是哪条，取输入里的第一条并标出
            edges.append(ids[0])
        metrics = {}
        for key, name in (("effectiveDistance", "effective_distance_m"), ("smokeTime", "smoke_time_s"), ("temporaryEscapeTime", "temporary_escape_time_s")):
            v = route.get(key)
            if not isinstance(v, int | float) or isinstance(v, bool) or not math.isfinite(v) or v < 0:
                raise ValueError(f"{key} 应为非负数值")
            metrics[name] = float(v)
        return {"nodes": [self.node_ids[i] for i in path], "edges": edges, "ambiguous_hops": ambiguous, **metrics}


def sort_key(route: dict[str, Any]) -> tuple[float, float, float]:
    """提供方的排序规则：暂时脱险时间 → 烟中时间 → 当量距离。"""
    return (route["temporary_escape_time_s"], route["smoke_time_s"], route["effective_distance_m"])


def best_route(per_target: list[dict[str, Any]]) -> dict[str, Any] | None:
    """各终点的首选路线中按排序规则最优的一条（附终点）；都没有路线返回 None。终点顺序决定并列时的先后。"""
    candidates = [{"target": t["target"], **t["routes"][0]} for t in per_target if t["status"] == "ok" and t["routes"]]
    return min(candidates, key=sort_key) if candidates else None
