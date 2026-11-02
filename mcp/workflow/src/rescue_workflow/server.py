"""workflow 服务：确定性流程。每个工具按固定步骤取数、计算、写研判快照，并返回由模板生成的报告事实部分。

只读：不发布任何指令，只写快照（var/snapshots/）和值守事件。见 docs/workflow-design.md。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from rescue_contract.client import MineData
from rescue_contract.models import Snapshot, SnapshotKind
from rescue_contract.serving import DEFAULT_PORTS
from rescue_contract.snapshots import SnapshotStore, new_id
from rescue_fire_model import explosion

from . import assess, reports

INSTRUCTIONS = (
    "工作流服务：研判、效果评估、升级建议、方案草案等标准流程按固定步骤由代码完成，结果写成带编号的研判快照。"
    "回答时把 report_markdown 原样单独成段放进回答，不改写、不删减；分析与建议另起一段，只引用报告里已有的数值。"
    "提交指令时 snapshot_id 照抄这里返回的值。"
)


def build_server(mine_data_target: Any | None = None, store: SnapshotStore | None = None) -> MCPServer:
    md = MineData(mine_data_target) if mine_data_target is not None else MineData.from_env()
    snapshots = store or SnapshotStore()
    xc = explosion.load_constants()
    public_url = (
        os.environ.get("RESCUE_FIRE_MODEL_PUBLIC_URL")
        or f"http://127.0.0.1:{os.environ.get('RESCUE_FIRE_MODEL_PORT', DEFAULT_PORTS['fire_model'])}"
    )
    server = MCPServer("workflow", instructions=INSTRUCTIONS)

    def save(kind: SnapshotKind, panel: str, as_of: str, inputs: dict, result: dict, report, parents: list[str] | None = None) -> Snapshot:
        at = datetime.fromisoformat(as_of)
        sid = new_id(at)
        snap = Snapshot(
            snapshot_id=sid, kind=kind, panel=panel, as_of=at, created_at=datetime.now().replace(microsecond=0),
            inputs=inputs, result=result, report_markdown=report(result, sid), parent_ids=parents or [],
        )  # fmt: skip
        return snapshots.put(snap)

    @server.tool()
    async def assess_panel(panel: str = "1201", include_personnel: bool = False) -> dict[str, Any]:
        """研判快照（标准研判一律先调它）：按固定步骤完成实时报警、预警等级和自燃发展阶段、触发指标的趋势、
        全部束管测点的爆炸三角形（取最不利）、受威胁人员（等级达到橙色或 include_personnel=true 时），
        写成研判快照并返回 snapshot_id、report_markdown（报告事实部分，含风险图）和结构化结果 result。
        回答时把 report_markdown 原样单独成段放进回答；不要另外再调 assess_warning_level、gas_trend、
        assess_explosion_risk 重复研判。阈值和判据为开发占位值。"""
        result = await assess.assess_panel(md, panel, xc, public_url, include_personnel)
        snap = save(
            SnapshotKind.ASSESSMENT,
            panel,
            result["as_of"],
            {"panel": panel, "include_personnel": include_personnel},
            result,
            reports.assessment,
        )
        return {"snapshot_id": snap.snapshot_id, "report_markdown": snap.report_markdown, "result": result}

    return server
