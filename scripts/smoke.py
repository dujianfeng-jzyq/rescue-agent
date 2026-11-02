#!/usr/bin/env python3
"""冒烟测试：逐个连接已启动的 MCP 服务，列出工具并调用关键工具；检查红线预检接口。

不需要 DSH 和大模型。用法（先 scripts/mcp-up.sh）：
    uv run --project mcp python scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime

from rescue_contract.client import connect
from rescue_contract.conformance import run_all

SERVERS = {
    "mine_data": os.environ.get("RESCUE_MCP_MINE_DATA_URL", "http://127.0.0.1:8101/mcp"),
    "ventnet": os.environ.get("RESCUE_MCP_VENTNET_URL", "http://127.0.0.1:8102/mcp"),
    "fire_model": os.environ.get("RESCUE_MCP_FIRE_MODEL_URL", "http://127.0.0.1:8103/mcp"),
    "dispatch": os.environ.get("RESCUE_MCP_DISPATCH_URL", "http://127.0.0.1:8104/mcp"),
    "kb": os.environ.get("RESCUE_MCP_KB_URL", "http://127.0.0.1:8105/mcp"),
    "actions": os.environ.get("RESCUE_MCP_ACTIONS_URL", "http://127.0.0.1:8106/mcp"),
}
REDLINE_URL = os.environ.get("RESCUE_REDLINE_CHECK_URL", "http://127.0.0.1:8106/redlines/check")

CALLS = {
    "mine_data": [("get_clock", {}), ("get_latest", {"sensor_ids": ["SM-1201-UC"]})],
    "ventnet": [("threatened_personnel", {"source_roadway_id": "R-1201-FACE"})],
    "fire_model": [("assess_warning_level", {}), ("check_realtime_alarms", {}), ("assess_explosion_risk", {"sensor_id": "BT-1201-G1"})],
    "dispatch": [("estimate_inert_gas", {"leak_m3_min": 40, "current_o2_pct": 9, "target_o2_pct": 6})],
    "kb": [("search_regulations", {"query": "封闭火区 惰性气体"})],
    "actions": [],  # 指令类工具不在冒烟测试里调用
}

failed = 0


def report(ok: bool, name: str, detail: str = "") -> None:
    global failed
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")


def short(x: object, n: int = 110) -> str:
    s = json.dumps(x, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "…"


async def check_server(key: str, url: str) -> None:
    try:
        async with connect(url) as c:
            tools = [t.name for t in (await c.list_tools()).tools]
            report(True, f"{key}: {len(tools)} 个工具", ", ".join(tools))
            for tool, args in CALLS[key]:
                r = await c.call_tool(tool, args)
                body = r.structured_content if r.structured_content is not None else [getattr(b, "text", "") for b in r.content]
                report(not r.is_error, f"{key}.{tool}", short(body))
    except Exception as exc:
        report(False, f"{key} 连接失败", f"{url}  {type(exc).__name__}: {exc}")


def check_redline_endpoint(now: str) -> None:
    plan = {
        "plan_id": "SMOKE",
        "kind": "sealing",
        "title": "冒烟测试：缺少注惰步骤的封闭方案",
        "context": {"fire_type": "goaf_spontaneous_combustion", "as_of": now, "explosion_risk": "unknown"},
        "steps": [{"order": 1, "type": "temporary_seal", "description": "临时密闭"}],
    }
    req = urllib.request.Request(
        REDLINE_URL,
        data=json.dumps({"tool": "issue_sealing_plan", "arguments": {"plan": plan}}).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {t}"} if (t := os.environ.get("RESCUE_MCP_TOKEN")) else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
        report(body["ok"] is False and any("第85条" in p for p in body["problems"]), "红线预检接口拦截缺少注惰的封闭方案", short(body["problems"]))
    except Exception as exc:
        report(False, "红线预检接口", str(exc))


async def main() -> int:
    for key, url in SERVERS.items():
        await check_server(key, url)
    for name, err in await run_all(SERVERS["mine_data"]):
        report(err is None, f"mine_data 一致性：{name}", err or "")
    try:
        async with connect(SERVERS["mine_data"]) as c:
            now = (await c.call_tool("get_clock", {})).structured_content["now"]
    except Exception:
        now = datetime.now().isoformat()
    check_redline_endpoint(now)
    print(f"\n{'全部通过' if not failed else f'{failed} 项失败'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
