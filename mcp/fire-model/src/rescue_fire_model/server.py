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

    async def realtime_alarms(panel: str) -> list[dict[str, Any]]:
        found = await alarms.check_methane(md) + await alarms.check_reignition(md, panel)
        return [asdict(a) for a in found]

    @server.tool()
    async def assess_warning_level(panel: str = "1201") -> dict[str, Any]:
        """按标志气体判定工作面采空区自燃预警等级（none/blue/yellow/orange/red），
        返回触发条件、指标值和数据质量说明。可疑数据已排除。

        预警等级反映自燃发展程度；实时红线报警是“立即行动”的触发条件，两者独立判定，
        报警不会抬高等级。realtime_alarms 一并给出同一时刻的报警，有报警时以报警为先。"""
        out = (await warning.assess(md, panel)).to_dict()
        out["realtime_alarms"] = await realtime_alarms(panel)
        return out

    @server.tool()
    async def gas_trend(sensor_id: str, measurand: Measurand, hours: float = 24) -> dict[str, Any]:
        """单个测点某参数的趋势：窗口内首个与最新可用值、变化量（最新减首个）、上升速率（每小时）、数据质量说明。
        报告“升温多少”“上升多少”时照抄 change，不要自己相减。"""
        now = await md.now()
        rs = await md.readings([sensor_id], start=now - timedelta(hours=hours), end=now, measurands=[measurand.value])
        sibling = None
        if measurand in (Measurand.C2H4, Measurand.C2H2):  # 与 assess_warning_level 一致：恒 0 时用同测点 CO 判断是否卡值
            co = await md.readings([sensor_id], start=now - timedelta(hours=hours), end=now, measurands=[Measurand.CO.value])
            sibling = sorted(co, key=lambda r: r.ts)
        rep = check_series(f"{sensor_id}/{measurand.value}", sorted(rs, key=lambda r: r.ts), sibling)
        first, last = (rep.usable[0], rep.usable[-1]) if rep.usable else (None, None)
        return {
            "sensor_id": sensor_id,
            "measurand": measurand.value,
            "points": len(rs),
            "usable_points": len(rep.usable),
            "first": first.value if first else None,
            "first_ts": first.ts.isoformat() if first else None,
            "latest": last.value if last else None,
            "latest_ts": last.ts.isoformat() if last else None,
            "change": round(last.value - first.value, 3) if first and last else None,
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
        """检查实时红线报警：甲烷超 2% 且上升（第75条）、封闭或启封后火区 CO 持续上升的复燃征兆（第164条，
        只对火区台账中的火区检查）。
        有报警时应立即提示指挥人员，不等待其他研判。"""
        return await realtime_alarms(panel)

    return server
