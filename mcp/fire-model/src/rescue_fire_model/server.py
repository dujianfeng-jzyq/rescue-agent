"""fire-model 服务：采空区自燃研判的专业计算。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer
from rescue_contract.client import MineData
from rescue_contract.models import Measurand

from . import alarms, explosion, warning
from .quality import check_series

INSTRUCTIONS = (
    "火灾专业计算服务。预警等级、指标值、爆炸危险性和实时报警都由确定性计算给出，"
    "引用时照抄数值并注明“计算结果”，不要自行估算或改写。阈值目前为演示占位值。"
)


def build_server(mine_data_target: Any | None = None) -> MCPServer:
    md = MineData(mine_data_target) if mine_data_target is not None else MineData.from_env()
    server = MCPServer("fire_model", instructions=INSTRUCTIONS)

    @server.tool()
    async def assess_warning_level(panel: str = "1201") -> dict[str, Any]:
        """按标志气体判定工作面采空区自燃预警等级（none/blue/yellow/orange/red），
        返回触发条件、指标值和数据质量说明。可疑数据已排除。"""
        return (await warning.assess(md, panel)).to_dict()

    @server.tool()
    async def gas_trend(sensor_id: str, measurand: Measurand, hours: float = 24) -> dict[str, Any]:
        """单个测点某参数的趋势：最新值、上升速率（每小时）、数据质量说明。"""
        now = await md.now()
        rs = await md.readings([sensor_id], start=now - timedelta(hours=hours), end=now, measurands=[measurand.value])
        rep = check_series(f"{sensor_id}/{measurand.value}", sorted(rs, key=lambda r: r.ts))
        return {
            "sensor_id": sensor_id,
            "measurand": measurand.value,
            "points": len(rs),
            "usable_points": len(rep.usable),
            "latest": rep.usable[-1].value if rep.usable else None,
            "latest_ts": rep.usable[-1].ts.isoformat() if rep.usable else None,
            "rate_per_hour": warning.slope_per_hour(rep.usable),
            "data_quality": rep.flags,
        }

    @server.tool()
    async def assess_explosion_risk(ch4_pct: float, o2_pct: float) -> dict[str, Any]:
        """用爆炸三角形判定甲烷—空气混合气体的爆炸危险性：
        explosive（处于爆炸范围）、may_become_explosive（混入空气后可能爆炸）、not_explosive。
        只考虑甲烷，火区存在其他可燃气体时结论偏乐观，需人工复核。"""
        risk = explosion.classify(ch4_pct, o2_pct)
        return {
            "ch4_pct": ch4_pct,
            "o2_pct": o2_pct,
            "explosion_risk": risk.value,
            "method": "爆炸三角形（仅甲烷），顶点取文献常用值",
            "caveat": "未计入 CO、H₂、乙烯等其他可燃组分",
        }

    @server.tool()
    async def check_realtime_alarms(panel: str = "1201") -> list[dict[str, Any]]:
        """检查实时红线报警：甲烷超 2% 且上升（第75条）、火区 CO 持续上升的复燃征兆（第164条）。
        有报警时应立即提示指挥人员，不等待其他研判。"""
        found = await alarms.check_methane(md) + await alarms.check_reignition(md, panel)
        return [asdict(a) for a in found]

    return server
