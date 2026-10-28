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
    MineClock,
    MineNetwork,
    PersonLocation,
    Reading,
    RescueTeam,
    ResourceItem,
    SensorPoint,
)

MINE_DATA_URL_ENV = "RESCUE_MCP_MINE_DATA_URL"
DEFAULT_MINE_DATA_URL = "http://127.0.0.1:8101/mcp"


class MineDataError(RuntimeError):
    pass


class MineData:
    """mine-data 服务的类型化客户端。

    `target` 可以是 URL，也可以是进程内的 MCPServer 实例（测试用）。
    """

    def __init__(self, target: Any) -> None:
        self._target = target

    @classmethod
    def from_env(cls) -> MineData:
        return cls(os.environ.get(MINE_DATA_URL_ENV, DEFAULT_MINE_DATA_URL))

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Client]:
        token = os.environ.get("RESCUE_MCP_TOKEN")
        if isinstance(self._target, str) and token:
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
                async with Client(streamable_http_client(self._target, http_client=http)) as c:
                    yield c
        else:
            async with Client(self._target) as c:
                yield c

    async def _call(self, tool: str, args: dict[str, Any]) -> Any:
        async with self._session() as c:
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

    async def personnel(self) -> list[PersonLocation]:
        return [PersonLocation.model_validate(x) for x in await self._call("get_personnel", {})]

    async def inventory(self) -> list[ResourceItem]:
        return [ResourceItem.model_validate(x) for x in await self._call("get_inventory", {})]

    async def rescue_teams(self) -> list[RescueTeam]:
        return [RescueTeam.model_validate(x) for x in await self._call("get_rescue_teams", {})]

    async def now(self) -> datetime:
        return MineClock.model_validate(await self._call("get_clock", {})).now
