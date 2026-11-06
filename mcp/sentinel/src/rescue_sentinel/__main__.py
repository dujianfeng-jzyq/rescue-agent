"""python -m rescue_sentinel —— 启动常驻监听器（不经模型）。

环境变量：RESCUE_MCP_MINE_DATA_URL（缺省 http://127.0.0.1:8101/mcp）、RESCUE_MCP_MINE_DATA_TOKEN（只读令牌，退回 RESCUE_MCP_TOKEN）、
RESCUE_INCIDENT_DIR（事件库）、RESCUE_SKILLS_DIR、RESCUE_SENTINEL_POLL_S（读矿井时钟的真实间隔，缺省 2 秒）。
每个周期单独连接矿井：矿井服务重启不需要重启监听器；连接或检测失败时记心跳里的错误，下个周期重试。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal

from rescue_contract.client import connect
from rescue_contract.collect import mcp_caller
from rescue_contract.incidents import IncidentStore

from .core import Sentinel

log = logging.getLogger("rescue_sentinel")


async def run() -> None:
    logging.basicConfig(level=os.environ.get("RESCUE_LOG_LEVEL", "INFO"))
    url = os.environ.get("RESCUE_MCP_MINE_DATA_URL", "http://127.0.0.1:8101/mcp")
    poll = float(os.environ.get("RESCUE_SENTINEL_POLL_S", "2"))
    sentinel = Sentinel(None, IncidentStore())  # type: ignore[arg-type]  # call 每个周期换成新连接的
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    log.info("监听器启动：工作面 %s，事件库 %s", sentinel.panels, sentinel.store.dir)
    while not stop.is_set():
        try:
            async with connect(url) as client:
                sentinel.call = mcp_caller(client)
                for a in await sentinel.tick():
                    log.warning("告警 %s：%s", a["event_id"], a["data"].get("summary"))
        except Exception as err:  # noqa: BLE001  监听器不能因为一次失败退出
            log.error("本周期失败：%s", err)
            sentinel.heartbeat(None, str(err)[:300])
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
