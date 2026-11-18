"""调用提供方服务：逐个终点 POST /api/v1/routes，超时、错误归类，结果换回我方编号。

服务不可用、超时、报错都不抛异常：返回 available=false 和原因（研判照常完成，界面显示“第二意见不可用”）。
只有我方输入不合法才抛 InputError（服务端转成 ToolError）。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx2

from .adapter import Indexed, PlanInput, best_route

ROUTES_PATH = "/api/v1/routes"
HEALTH_PATH = "/healthz"
URL_ENV = "RESCUE_ROUTE_SERVICE_URL"
TOKEN_ENV = "RESCUE_ROUTE_SERVICE_TOKEN"
TIMEOUT_ENV = "RESCUE_ROUTE_SERVICE_TIMEOUT_S"
DEFAULT_TIMEOUT_S = 5.0
MAX_CONCURRENT = 4  # 提供方是单台服务器；终点通常只有几个
RAW_TEXT_LIMIT = 2000


@dataclass(frozen=True)
class Config:
    url: str | None
    token: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S

    @classmethod
    def from_env(cls) -> Config:
        url = (os.environ.get(URL_ENV) or "").strip().rstrip("/") or None
        timeout = float(os.environ.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT_S)
        return cls(url=url, token=os.environ.get(TOKEN_ENV) or None, timeout_s=timeout)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}


def _body(resp: httpx2.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:RAW_TEXT_LIMIT]


async def _call_one(http: httpx2.AsyncClient, cfg: Config, idx: Indexed, base: dict[str, Any], target: str, gate: asyncio.Semaphore) -> dict[str, Any]:
    out: dict[str, Any] = {"target": target, "status": "error", "http_status": None, "error": None, "routes": [], "version": None, "raw_response": None}
    t0 = time.monotonic()
    try:
        async with gate:
            resp = await http.post(f"{cfg.url}{ROUTES_PATH}", json={**base, "target": idx.node_index[target]}, headers=cfg.headers())
    except httpx2.TimeoutException:
        out["error"] = f"超时（{cfg.timeout_s:g} 秒）"
    except httpx2.HTTPError as err:
        out["error"] = f"连接失败：{type(err).__name__}: {err}"
    else:
        body = _body(resp)
        out["http_status"] = resp.status_code
        out["raw_response"] = body
        if resp.status_code != 200:
            msg = body.get("error") if isinstance(body, dict) else None
            out["error"] = f"服务返回 {resp.status_code}" + (f"：{msg}" if msg else "")
        elif not isinstance(body, dict) or not isinstance(body.get("routes"), list):
            out["error"] = "返回格式不对：没有 routes 列表"
        else:
            out["version"] = body.get("version") if isinstance(body.get("version"), str) else None
            try:
                out["routes"] = [idx.convert_route(r, target) for r in body["routes"]]
            except ValueError as err:
                out["error"] = f"返回路线不合法：{err}"
            else:
                out["status"] = "ok" if out["routes"] else "empty"
                if not out["routes"]:
                    # 提供方目前不区分“不可达”和“烟中时间超限”（第 6 节第 1 条）；给出原因字段时原样带上
                    out["empty_reason"] = body.get("reason") if isinstance(body.get("reason"), str) else None
    out["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    return out


async def plan(inp: PlanInput, cfg: Config, *, transport: httpx2.AsyncBaseTransport | None = None) -> dict[str, Any]:
    idx = Indexed(inp)  # 输入不合法在这里抛 InputError
    base = idx.provider_request()
    result: dict[str, Any] = {
        "available": False, "error": None, "service_url": cfg.url, "service_version": None, "elapsed_ms": 0,
        "start": inp.start, "best": None, "targets": [], "provider_request": base,
    }  # fmt: skip
    if not cfg.url:
        result["error"] = f"未配置（{URL_ENV} 未设置）"
        return result
    t0 = time.monotonic()
    gate = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx2.AsyncClient(timeout=cfg.timeout_s, transport=transport) as http:
        per_target = await asyncio.gather(*(_call_one(http, cfg, idx, base, t, gate) for t in inp.targets))
    result["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    result["targets"] = list(per_target)
    answered = [t for t in per_target if t["status"] in ("ok", "empty")]
    versions = sorted({t["version"] for t in answered if t["version"]})
    result["service_version"] = "、".join(versions) or None
    if answered:
        result["available"] = True
        result["best"] = best_route(per_target)
        failed = [t for t in per_target if t["status"] == "error"]
        if failed:
            result["error"] = "部分终点调用失败：" + "；".join(f"{t['target']}：{t['error']}" for t in failed)
    else:
        result["error"] = "；".join(sorted({t["error"] for t in per_target if t["error"]})) or "服务没有返回结果"
    return result


async def health(cfg: Config, *, transport: httpx2.AsyncBaseTransport | None = None) -> tuple[bool, str]:
    if not cfg.url:
        return False, f"未配置（{URL_ENV} 未设置）"
    try:
        async with httpx2.AsyncClient(timeout=cfg.timeout_s, transport=transport) as http:
            resp = await http.get(f"{cfg.url}{HEALTH_PATH}", headers=cfg.headers())
    except httpx2.HTTPError as err:
        return False, f"{type(err).__name__}: {err}"
    return resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
