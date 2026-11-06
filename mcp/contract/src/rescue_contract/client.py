"""数据契约客户端：计算类服务器用它向 mine-data 服务取数，不经过大模型。

mine-data 服务（模拟矿井或真实矿井适配器）对外只暴露 MCP 工具；这里把工具调用
包装成类型化方法。接入真实矿井时只要适配器实现同名工具，本客户端和所有计算服务都不用改。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from .models import (
    DispatchRequest,
    EquipmentStatus,
    FireArea,
    MineAlarm,
    MineCapabilities,
    MineClock,
    MineDescription,
    MineNetwork,
    MineReceipt,
    NoticeRequest,
    PersonLocation,
    PersonnelSnapshot,
    Reading,
    RescueTeam,
    ResourceItem,
    SensorPoint,
    TreatmentRequest,
)
from .serving import auth_headers, mine_act_headers

MINE_DATA_URL_ENV = "RESCUE_MCP_MINE_DATA_URL"
DEFAULT_MINE_DATA_URL = "http://127.0.0.1:8101/mcp"


class MineDataError(RuntimeError):
    pass


@asynccontextmanager
async def connect(target: Any, service: str = "mine_data", act: bool = False) -> AsyncIterator[Client]:
    """打开到 MCP 服务的客户端会话。

    `target` 是 URL 时，按 `service` 带上该服务的 Bearer 令牌（RESCUE_MCP_<服务>_TOKEN 或 RESCUE_MCP_TOKEN，见 serving）；
    也可以是进程内的 MCPServer 实例（测试用）。所有 Python 侧的 MCP 连接都应经过这里。
    `act=True` 带写入令牌（rescue-mine 的写入类工具，只有 guard 用，见 serving.mine_act_headers）。
    """
    headers = mine_act_headers() if act else auth_headers(service)
    if isinstance(target, str) and headers:
        async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
            async with Client(streamable_http_client(target, http_client=http)) as c:
                yield c
    else:
        async with Client(target) as c:
            yield c


class MineData:
    """mine-data 服务的类型化客户端。

    `target` 可以是 URL，也可以是进程内的 MCPServer 实例（测试用）。
    """

    def __init__(self, target: Any) -> None:
        self._target = target

    @classmethod
    def from_env(cls) -> MineData:
        return cls(os.environ.get(MINE_DATA_URL_ENV, DEFAULT_MINE_DATA_URL))

    async def _call(self, tool: str, args: dict[str, Any], act: bool = False) -> Any:
        async with connect(self._target, act=act) as c:
            r = await c.call_tool(tool, args)
        if r.is_error:
            text = " ".join(getattr(b, "text", "") for b in r.content)
            raise MineDataError(f"mine_data.{tool} 失败：{text}")
        if r.structured_content is not None:
            sc = r.structured_content
            return sc["result"] if set(sc) == {"result"} else sc
        # 没有结构化输出时退回解析文本（适配器可能用其他 SDK 实现）
        text = "".join(getattr(b, "text", "") for b in r.content)
        return json.loads(text)

    async def network(self) -> MineNetwork:
        return MineNetwork.model_validate(await self._call("get_network", {}))

    async def sensors(self) -> list[SensorPoint]:
        return [SensorPoint.model_validate(x) for x in await self._call("list_sensors", {})]

    async def readings(
        self,
        sensor_ids: list[str],
        start: datetime | None = None,
        end: datetime | None = None,
        measurands: list[str] | None = None,
    ) -> list[Reading]:
        args: dict[str, Any] = {"sensor_ids": sensor_ids}
        if start:
            args["start"] = start.isoformat()
        if end:
            args["end"] = end.isoformat()
        if measurands:
            args["measurands"] = measurands
        return [Reading.model_validate(x) for x in await self._call("get_readings", args)]

    async def latest(self, sensor_ids: list[str]) -> list[Reading]:
        return [Reading.model_validate(x) for x in await self._call("get_latest", {"sensor_ids": sensor_ids})]

    async def personnel_snapshot(self) -> PersonnelSnapshot:
        return PersonnelSnapshot.model_validate(await self._call("get_personnel", {}))

    async def personnel(self) -> list[PersonLocation]:
        return (await self.personnel_snapshot()).people

    async def inventory(self) -> list[ResourceItem]:
        return [ResourceItem.model_validate(x) for x in await self._call("get_inventory", {})]

    async def rescue_teams(self) -> list[RescueTeam]:
        return [RescueTeam.model_validate(x) for x in await self._call("get_rescue_teams", {})]

    async def fire_areas(self) -> list[FireArea]:
        return [FireArea.model_validate(x) for x in await self._call("get_fire_areas", {})]

    async def now(self) -> datetime:
        return MineClock.model_validate(await self._call("get_clock", {})).now

    # ── 自描述、报警、设备（第三版）──

    async def capabilities(self) -> MineCapabilities:
        return MineCapabilities.model_validate(await self._call("get_capabilities", {}))

    async def describe(self) -> MineDescription:
        return MineDescription.model_validate(await self._call("describe_mine", {}))

    async def alarms(self, hours: float = 24, active_only: bool = False) -> list[MineAlarm]:
        return [MineAlarm.model_validate(x) for x in await self._call("get_alarms", {"hours": hours, "active_only": active_only})]

    async def equipment(self) -> list[EquipmentStatus]:
        return [EquipmentStatus.model_validate(x) for x in await self._call("get_equipment_status", {})]

    # ── 通知与指令（写入令牌；只有 guard 调用）──

    async def send_notice(self, request: NoticeRequest) -> MineReceipt:
        return MineReceipt.model_validate(await self._call("send_notice", {"request": request.model_dump(mode="json")}, act=True))

    async def dispatch_order(self, request: DispatchRequest) -> MineReceipt:
        return MineReceipt.model_validate(await self._call("dispatch_order", {"request": request.model_dump(mode="json")}, act=True))

    async def submit_treatment(self, request: TreatmentRequest) -> MineReceipt:
        return MineReceipt.model_validate(await self._call("submit_treatment", {"request": request.model_dump(mode="json")}, act=True))

    async def action_status(self, action_id: str) -> MineReceipt:
        return MineReceipt.model_validate(await self._call("get_action_status", {"action_id": action_id}))
