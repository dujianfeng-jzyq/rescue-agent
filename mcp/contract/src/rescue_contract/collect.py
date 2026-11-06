"""按技能的采集清单（references/collect.yaml）从 mine_data 取数，整理成证据内容（Python 采集小工具）。

与证据插件的 dsh-bundle/src/evidence/collect.ts 规则相同：不经 DSH 的确定性基线评测用它取数、运行同一批技能脚本。
两边对同一时刻的模拟矿井采集，证据内容的规范化 JSON 必须逐字节相同（tests/test_collect_parity.py）：
- 测点按 id 排序，读数按 (sensor_id, measurand, ts) 排序；
- 时间截到秒；窗口起点 = as_of − window_h；
- 只用 mine_data 的工具（CLAUDE.md 约定 5），工具返回的结构化数据原样保留。

命令行：
    python -m rescue_contract.collect <collect.yaml> <kind> <panel> [--url URL]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

CallTool = Callable[[str, dict[str, Any]], Awaitable[Any]]


def parse_collect_spec(text: str, where: str = "collect.yaml") -> dict[str, Any]:
    spec = yaml.safe_load(text)
    if not isinstance(spec, dict):
        raise ValueError(f"{where}：不是映射")
    if spec.get("status") not in ("placeholder", "reviewed"):
        raise ValueError(f"{where}：顶层要有 status: placeholder 或 reviewed")
    if not isinstance(spec.get("kinds"), dict):
        raise ValueError(f"{where}：缺少 kinds")
    for kind, k in spec["kinds"].items():
        for g in (k or {}).get("sensors") or []:
            if not g.get("name") or not isinstance(g.get("select"), dict):
                raise ValueError(f"{where}：{kind} 的测点组要有 name 和 select")
            _check_select(g["select"], f"{where}：{kind} 的测点组 {g['name']}")
    return spec


SELECT_KEYS = {"role", "source", "panel"}


def _check_select(sel: dict[str, Any], where: str) -> None:
    """select 只认 role（语义角色）、source（测点来源）、panel（true：只取本工作面的测点）。
    不再按测点编号取数：换矿时测点编号会变，角色由 describe_mine/list_sensors 如实回答。"""
    if "ids" in sel:
        raise ValueError(f"{where}：select 不再支持 ids，请改用 role（按语义角色取数）")
    unknown = set(sel) - SELECT_KEYS
    if unknown:
        raise ValueError(f"{where}：select 里有不认识的键 {sorted(unknown)}（只认 {sorted(SELECT_KEYS)}）")
    if not sel.get("role") and not sel.get("source"):
        raise ValueError(f"{where}：select 至少要有 role 或 source")


def mine_time_to_seconds(t: str) -> str:
    return datetime.fromisoformat(t).strftime("%Y-%m-%dT%H:%M:%S")


def minus_hours(as_of: str, hours: float) -> str:
    # 与 TS 侧一致：按毫秒取整
    t = datetime.fromisoformat(mine_time_to_seconds(as_of)).replace(tzinfo=UTC) - timedelta(milliseconds=round(hours * 3_600_000))
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def _unwrap(v: Any) -> Any:
    if isinstance(v, dict) and len(v) == 1 and isinstance(v.get("result"), list):
        return v["result"]
    return v


def _sort_readings(rs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rs, key=lambda r: (r["sensor_id"], r["measurand"], r["ts"]))


def _select(sensors: list[dict[str, Any]], g: dict[str, Any], panel: str) -> list[dict[str, Any]]:
    sel = g["select"]
    req = g.get("require_measurands") or []
    out = [
        s for s in sensors
        if (not sel.get("role") or s.get("role") == sel["role"])
        and (not sel.get("source") or s["source"] == sel["source"])
        and (not sel.get("panel") or s.get("panel") == panel)
        and all(m in s["measurands"] for m in req)
    ]  # fmt: skip
    return sorted(out, key=lambda s: s["id"])


def since_window_h(as_of: str, since: str, before_h: float) -> float:
    """从 since 之前 before_h 小时到 as_of 的窗口长度（小时）。与 TS 侧同一算法：整秒差 / 3600 + before_h。"""
    secs = (datetime.fromisoformat(as_of) - datetime.fromisoformat(since)).total_seconds()
    if secs < 0:
        raise ValueError(f"处置开始时间 {since} 晚于矿井当前时间 {as_of}")
    return int(secs) / 3600 + before_h


async def collect(call: CallTool, spec: dict[str, Any], kind: str, panel: str, since: str | None = None) -> dict[str, Any]:
    """since：处置开始的矿井时间；测点组写了 before_since_h 时，窗口从 since 之前这么多小时取到现在（复评用）。"""
    k = spec["kinds"].get(kind)
    if k is None:
        raise ValueError(f"采集清单没有 {kind}（有：{'、'.join(spec['kinds'])}）")
    as_of = mine_time_to_seconds(str((await call("get_clock", {}))["now"]))
    ev: dict[str, Any] = {"kind": kind, "panel": panel, "as_of": as_of, "groups": {}, "missing": []}
    if since is not None:
        since = mine_time_to_seconds(since)
        ev["since"] = since
    groups = k.get("sensors") or []
    sensors = _unwrap(await call("list_sensors", {})) if groups else []
    for g in groups:
        picked = _select(sensors, g, panel)
        window_h = g.get("window_h") or 0
        if g.get("before_since_h") is not None:
            if since is None:
                raise ValueError(f"采集清单 {kind} 的测点组 {g['name']} 按处置开始时间取窗口，需要给出 since")
            window_h = since_window_h(as_of, since, g["before_since_h"])
        wanted_by = {s["id"]: sorted(m for m in s["measurands"] if g.get("measurands") is None or m in g["measurands"]) for s in picked}
        ids = [s["id"] for s in picked]
        latest: list[dict[str, Any]] = []
        readings: list[dict[str, Any]] = []
        if ids:
            latest = [r for r in _unwrap(await call("get_latest", {"sensor_ids": ids})) if r["measurand"] in wanted_by.get(r["sensor_id"], [])]
            if window_h > 0:
                wanted = sorted({m for ms in wanted_by.values() for m in ms})
                args = {"sensor_ids": ids, "start": minus_hours(as_of, window_h), "end": as_of, "measurands": wanted}
                readings = [r for r in _unwrap(await call("get_readings", args)) if r["measurand"] in wanted_by.get(r["sensor_id"], [])]
        elif g.get("required"):
            ev["missing"].append({"group": g["name"], "reason": "没有匹配的测点"})
        ev["groups"][g["name"]] = {
            "sensors": picked,
            "measurands": wanted_by,
            "latest": _sort_readings(latest),
            "readings": _sort_readings(readings),
            "window_h": window_h,
        }
    if k.get("events_h"):
        ev["events"] = _unwrap(await call("get_events", {"hours": k["events_h"]}))
    for key, tool in (("fire_areas", "get_fire_areas"), ("personnel", "get_personnel"), ("inventory", "get_inventory"), ("rescue_teams", "get_rescue_teams"), ("network", "get_network")):
        if k.get(key):
            ev[key] = _unwrap(await call(tool, {}))
    return ev


def mcp_caller(client: Any) -> CallTool:
    """把 MCP 客户端（rescue_contract.client.connect 打开的）包成 CallTool：返回结构化结果，工具报错时抛 RuntimeError。"""

    async def call(tool: str, args: dict[str, Any]) -> Any:
        r = await client.call_tool(tool, args)
        if r.is_error:
            raise RuntimeError(f"{tool}：{' '.join(getattr(b, 'text', '') for b in r.content) or '工具报错'}")
        if r.structured_content is not None:
            return r.structured_content
        return json.loads("".join(getattr(b, "text", "") for b in r.content))

    return call


async def collect_from(target: Any, spec: dict[str, Any], kind: str, panel: str, since: str | None = None) -> dict[str, Any]:
    """target 是 URL（带 RESCUE_MCP_TOKEN）或进程内的 MCPServer。"""
    from .client import connect

    async with connect(target) as c:
        return await collect(mcp_caller(c), spec, kind, panel, since)


async def _main(args: argparse.Namespace) -> int:
    from .canonical import content_sha256

    spec = parse_collect_spec(Path(args.spec).read_text(encoding="utf-8"), args.spec)
    ev = await collect_from(args.url, spec, args.kind, args.panel)
    print(json.dumps({"content_sha256": content_sha256(ev), "evidence": ev}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rescue_contract.collect")
    ap.add_argument("spec")
    ap.add_argument("kind")
    ap.add_argument("panel")
    ap.add_argument("--url", default=os.environ.get("RESCUE_MCP_MINE_DATA_URL", "http://127.0.0.1:8101/mcp"))
    return asyncio.run(_main(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
