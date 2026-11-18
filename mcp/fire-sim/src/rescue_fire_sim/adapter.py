"""我方编号 ↔ 提供方服务（MineFireSimulation）的数组下标：输入校验、请求转换、返回结果校验和压缩。

纯函数，不访问网络（网络调用在 service.py）。提供方接口见 docs/design-v7-fire-sim.md 第 3、5 节：
- 输入 branches / nodes 用数组下标（index），巷道长度由两端节点坐标计算，z 参与火风压计算；
- 通风边界：mode 1 压入式（inletFlows：进风节点 → m³/s）、mode 2 抽出式（outletPressures：出风节点 → Pa）；
- 返回 windSpeed（每个时间步各巷道的风速 m/s）和 coConcentration（每条巷道按约 2 m 划分网格，各时间步各网格点的 CO，vol%，
  保留 3 位小数，即分辨率 10 ppm；整条巷道全为 0 时 values 是数字 0）。

压缩只做数据整理，不做判断（没有阈值）：每条巷道每个时间步的风速和全巷道最大 CO（换成 ppm）、首次出现 CO 的时刻、
CO 峰值、风向是否反转。判读（“到达”“危险”）在技能脚本里按参数做（CLAUDE.md 约定 1）。
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

FUEL_TYPES = {0: "煤", 1: "木材", 2: "输送带", 3: "柴油", 4: "电缆", 5: "PVC"}
MODES = {"forced": 1, "exhaust": 2}
BOUNDARY_MARKERS = {None: 0, "inlet": 1, "outlet": 2}
MAX_FRAMES = 721  # 输出帧数上限（如 12 小时 × 每分钟一帧）：结果按帧数和网格数增长，防止一次调用拿回几百 MB
VOL_PCT_TO_PPM = 10_000


class Node(BaseModel):
    id: str = Field(description="节点编号（我方编号）")
    xyz: list[float] = Field(description="[x, y, z]，单位 m；z 是标高，参与火风压计算")


class Branch(BaseModel):
    id: str = Field(description="巷道编号（我方编号；同一条巷道拆成几段时各段编号不同）")
    from_node: str = Field(description="起点节点编号")
    to_node: str = Field(description="终点节点编号")
    resistance: float = Field(description="风阻，N·s²/m⁸，> 0")
    area_m2: float = Field(description="净断面面积，m²，> 0")
    closed: bool = Field(default=False, description="风门关闭（该巷道风量为 0）")
    fixed_flow_m3_s: float = Field(default=0.0, description="强制风量，m³/s；0 表示不强制")
    boundary: Literal["inlet", "outlet"] | None = Field(default=None, description="inlet：起点是进风边界；outlet：终点是出风边界")


class Fire(BaseModel):
    branch: str = Field(description="火源所在巷道（branches 里的编号）")
    position: float = Field(description="火源在巷道上的相对位置，0 = 起点、1 = 终点")
    total_time_s: float = Field(description="推演时长，s")
    output_interval_s: float = Field(description="输出间隔，s")
    fuel_type: int = Field(description="燃料类型：0 煤、1 木材、2 输送带、3 柴油、4 电缆、5 PVC")
    burning_area_m2: float = Field(description="燃烧面积，m²")
    fuel_load_kg: float = Field(description="燃料质量，kg")
    oxygen_pct: float = Field(description="入风氧气体积浓度，%")


class Boundary(BaseModel):
    mode: Literal["forced", "exhaust"] = Field(description="forced 压入式（给进风量）、exhaust 抽出式（给出风压力）")
    inlet_flows_m3_s: dict[str, float] = Field(default_factory=dict, description="进风节点 → 风量 m³/s（forced 时必填）")
    outlet_pressures_pa: dict[str, float] = Field(default_factory=dict, description="出风节点 → 压力 Pa（exhaust 时必填）")


class SimInput(BaseModel):
    nodes: list[Node]
    branches: list[Branch]
    fire: Fire
    boundary: Boundary


class InputError(ValueError):
    """输入不合法（服务端转成 ToolError）。"""


def _finite(*vs: float) -> bool:
    return all(isinstance(v, int | float) and math.isfinite(v) for v in vs)


def _dups(ids: list[str]) -> list[str]:
    return sorted({i for i in ids if ids.count(i) > 1})


def validate(inp: SimInput) -> None:
    problems: list[str] = []
    ids = [n.id for n in inp.nodes]
    if not ids:
        problems.append("没有节点")
    if dup := _dups(ids):
        problems.append(f"节点编号重复：{'、'.join(dup)}")
    known = set(ids)
    for n in inp.nodes:
        if len(n.xyz) != 3 or not _finite(*n.xyz):
            problems.append(f"节点 {n.id} 的坐标应为 3 个有限数值")
    bids = [b.id for b in inp.branches]
    if not bids:
        problems.append("没有巷道")
    if dup := _dups(bids):
        problems.append(f"巷道编号重复：{'、'.join(dup)}")
    for b in inp.branches:
        for end in (b.from_node, b.to_node):
            if end not in known:
                problems.append(f"巷道 {b.id} 引用了未知节点 {end}")
        if b.from_node == b.to_node:
            problems.append(f"巷道 {b.id} 的起点和终点相同")
        if not (_finite(b.resistance) and b.resistance > 0):
            problems.append(f"巷道 {b.id} 的风阻应大于 0")
        if not (_finite(b.area_m2) and b.area_m2 > 0):
            problems.append(f"巷道 {b.id} 的断面应大于 0")
        if not (_finite(b.fixed_flow_m3_s) and b.fixed_flow_m3_s >= 0):
            problems.append(f"巷道 {b.id} 的强制风量不能为负")
    f = inp.fire
    if f.branch not in set(bids):
        problems.append(f"火源巷道 {f.branch} 不在 branches 里")
    if not (_finite(f.position) and 0 <= f.position <= 1):
        problems.append("火源位置应在 [0, 1]")
    if f.fuel_type not in FUEL_TYPES:
        problems.append(f"燃料类型应为 0–5（{'、'.join(f'{k} {v}' for k, v in FUEL_TYPES.items())}）")
    if not (_finite(f.total_time_s, f.output_interval_s) and f.total_time_s > 0 and 0 < f.output_interval_s <= f.total_time_s):
        problems.append("推演时长应大于 0，输出间隔应大于 0 且不超过推演时长")
    elif f.total_time_s / f.output_interval_s + 1 > MAX_FRAMES:
        problems.append(f"输出帧数超过上限 {MAX_FRAMES}（推演时长 ÷ 输出间隔 + 1）")
    if not (_finite(f.burning_area_m2) and f.burning_area_m2 > 0):
        problems.append("燃烧面积应大于 0")
    if not (_finite(f.fuel_load_kg) and f.fuel_load_kg > 0):
        problems.append("燃料质量应大于 0")
    if not (_finite(f.oxygen_pct) and 0 < f.oxygen_pct <= 100):
        problems.append("入风氧气浓度应在 (0, 100]")
    bd = inp.boundary
    table = bd.inlet_flows_m3_s if bd.mode == "forced" else bd.outlet_pressures_pa
    if not table:
        problems.append("压入式要给进风节点风量" if bd.mode == "forced" else "抽出式要给出风节点压力")
    for nid, v in {**bd.inlet_flows_m3_s, **bd.outlet_pressures_pa}.items():
        if nid not in known:
            problems.append(f"通风边界引用了未知节点 {nid}")
        if not _finite(v):
            problems.append(f"通风边界节点 {nid} 的数值不是有限数")
    if bd.mode == "forced" and any(v <= 0 for v in bd.inlet_flows_m3_s.values() if _finite(v)):
        problems.append("进风量应大于 0")
    if problems:
        raise InputError("；".join(problems))


class Indexed:
    """一次输入的编号 ↔ 下标对照。"""

    def __init__(self, inp: SimInput) -> None:
        validate(inp)
        self.inp = inp
        self.node_index = {n.id: i for i, n in enumerate(inp.nodes)}
        self.branch_ids = [b.id for b in inp.branches]
        self.branch_index = {bid: i for i, bid in enumerate(self.branch_ids)}

    def provider_request(self) -> dict[str, Any]:
        ni, f, bd = self.node_index, self.inp.fire, self.inp.boundary
        return {
            "fireSource": {
                "branchId": self.branch_index[f.branch], "position": f.position, "totalTime": f.total_time_s,
                "outputInterval": f.output_interval_s, "fuelType": f.fuel_type, "burningArea": f.burning_area_m2,
                "fuelLoad": f.fuel_load_kg, "oxygenConcentration": f.oxygen_pct,
            },
            "ventBoundary": {
                "mode": MODES[bd.mode],
                "inletFlows": {str(ni[k]): v for k, v in bd.inlet_flows_m3_s.items()},
                "outletPressures": {str(ni[k]): v for k, v in bd.outlet_pressures_pa.items()},
            },
            "branches": [
                {
                    "index": i, "from": ni[b.from_node], "to": ni[b.to_node], "resistance": b.resistance, "fixedFlow": b.fixed_flow_m3_s,
                    "closed": b.closed, "area": b.area_m2, "boundaryMarker": BOUNDARY_MARKERS[b.boundary],
                }
                for i, b in enumerate(self.inp.branches)
            ],
            "nodes": [{"index": i, "x": n.xyz[0], "y": n.xyz[1], "z": n.xyz[2]} for i, n in enumerate(self.inp.nodes)],
        }  # fmt: skip

    def summarize(self, body: Any) -> dict[str, Any]:
        """校验提供方返回并压缩成每条巷道的时间序列（我方编号）；格式不对抛 ValueError（说明原因）。"""
        if not isinstance(body, dict):
            raise ValueError("返回不是对象")
        ws, co = body.get("windSpeed"), body.get("coConcentration")
        if not isinstance(ws, dict) or not isinstance(co, dict):
            raise ValueError("缺少 windSpeed 或 coConcentration")
        n = len(self.branch_ids)
        if ws.get("valueUnit") not in (None, "m/s") or co.get("valueUnit") not in (None, "vol%"):
            raise ValueError(f"单位变了：风速 {ws.get('valueUnit')}、CO {co.get('valueUnit')}（应为 m/s、vol%）")
        order = ws.get("branchIds")
        if not isinstance(order, list) or sorted(order) != list(range(n)):
            raise ValueError("windSpeed.branchIds 与请求的巷道不一致")
        rows = ws.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ValueError("windSpeed 没有时间步")
        times = [r.get("time") if isinstance(r, dict) else None for r in rows]
        if not _finite(*times) or times != sorted(times):
            raise ValueError("windSpeed 的时间不是递增的数值")
        speed: list[list[float]] = [[] for _ in range(n)]
        for r in rows:
            vals = r.get("values")
            if not isinstance(vals, list) or len(vals) != n or not _finite(*vals):
                raise ValueError(f"windSpeed 第 {r.get('time')} 秒的风速个数或数值不对")
            for k, v in zip(order, vals, strict=True):
                speed[k].append(float(v))
        grids = co.get("branches")
        if not isinstance(grids, list) or sorted(g.get("branchId") for g in grids if isinstance(g, dict)) != list(range(n)):
            raise ValueError("coConcentration.branches 与请求的巷道不一致")
        co_max: list[list[float]] = [[] for _ in range(n)]
        for g in grids:
            k = g["branchId"]
            cells = len(g.get("positions") or [])
            grow = g.get("rows")
            if not isinstance(grow, list) or [r.get("time") for r in grow] != times:
                raise ValueError(f"巷道 {self.branch_ids[k]} 的 CO 时间步与风速不一致")
            for r in grow:
                v = r.get("values")
                if isinstance(v, int | float) and not isinstance(v, bool):
                    vals = [float(v)]  # 全零时压缩成一个数字
                elif isinstance(v, list) and len(v) == cells and v:
                    vals = v
                else:
                    raise ValueError(f"巷道 {self.branch_ids[k]} 第 {r.get('time')} 秒的 CO 网格数不对")
                if not _finite(*vals) or min(vals) < 0:
                    raise ValueError(f"巷道 {self.branch_ids[k]} 第 {r.get('time')} 秒的 CO 有非法数值")
                co_max[k].append(round(max(vals) * VOL_PCT_TO_PPM, 1))
        branches = []
        for k, bid in enumerate(self.branch_ids):
            s, c = speed[k], co_max[k]
            first = next((t for t, v in zip(times, c, strict=True) if v > 0), None)
            branches.append({
                "id": bid, "air_speed_m_s": s, "co_max_ppm": c, "co_first_s": first, "co_peak_ppm": max(c),
                "air_reversed": any(v * s[0] < 0 for v in s), "air_stopped": any(v == 0 for v in s) and s[0] != 0,
            })  # fmt: skip
        return {"times_s": [float(t) for t in times], "branches": branches}
