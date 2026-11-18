"""调用提供方服务：POST /FireSimulation/GetMineFireSimulationResult，超时、错误归类，结果压缩并换回我方编号。

服务不可用、超时、报错都不抛异常：返回 available=false 和原因（研判照常完成，报告写“第二意见不可用”）。
只有我方输入不合法才抛 InputError（服务端转成 ToolError）。

原始返回很大（按巷道长度每 2 m 一个网格、每帧一组），不整个带回：带回压缩结果（adapter.summarize）和原始返回的
SHA-256、字节数。服务是确定性的（同一输入结果逐字节相同，2026-11-17 实测），复盘时用存档的 provider_request 重算核对。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx2

from .adapter import Indexed, SimInput

SIM_PATH = "/FireSimulation/GetMineFireSimulationResult"
SPEC_PATH = "/swagger/v1/swagger.json"  # 服务没有健康检查接口，用接口说明代替
URL_ENV = "RESCUE_FIRE_SIM_URL"
TOKEN_ENV = "RESCUE_FIRE_SIM_TOKEN"
TIMEOUT_ENV = "RESCUE_FIRE_SIM_TIMEOUT_S"
DEFAULT_TIMEOUT_S = 60.0  # 实测 1 小时推演约 1 秒、2 小时约 4 秒（47 条巷道）；真实矿井网络更大
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


def _error_text(resp: httpx2.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:RAW_TEXT_LIMIT]
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    if isinstance(body, dict) and isinstance(body.get("title"), str):  # ASP.NET 的模型校验错误
        return f"{body['title']} {json.dumps(body.get('errors'), ensure_ascii=False)[:RAW_TEXT_LIMIT]}"
    return json.dumps(body, ensure_ascii=False)[:RAW_TEXT_LIMIT]


async def simulate(inp: SimInput, cfg: Config, *, transport: httpx2.AsyncBaseTransport | None = None) -> dict[str, Any]:
    idx = Indexed(inp)  # 输入不合法在这里抛 InputError
    req = idx.provider_request()
    result: dict[str, Any] = {
        "available": False, "error": None, "service_url": cfg.url, "service_version": None, "http_status": None, "elapsed_ms": 0,
        "provider_request": req, "result_sha256": None, "result_bytes": None, "times_s": [], "branches": [],
    }  # fmt: skip
    if not cfg.url:
        result["error"] = f"未配置（{URL_ENV} 未设置）"
        return result
    t0 = time.monotonic()
    try:
        async with httpx2.AsyncClient(timeout=cfg.timeout_s, transport=transport) as http:
            resp = await http.post(f"{cfg.url}{SIM_PATH}", json=req, headers=cfg.headers())
    except httpx2.TimeoutException:
        result["error"] = f"超时（{cfg.timeout_s:g} 秒）"
    except httpx2.HTTPError as err:
        result["error"] = f"连接失败：{type(err).__name__}: {err}"
    else:
        result["http_status"] = resp.status_code
        raw = resp.content
        if resp.status_code != 200:
            result["error"] = f"服务返回 {resp.status_code}：{_error_text(resp)}"
        else:
            result["result_sha256"] = hashlib.sha256(raw).hexdigest()
            result["result_bytes"] = len(raw)
            try:
                result.update(idx.summarize(json.loads(raw)))
            except ValueError as err:  # json.JSONDecodeError 也是 ValueError
                result["error"] = f"返回格式不对：{err}"
            else:
                result["available"] = True
    result["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    return result


async def health(cfg: Config, *, transport: httpx2.AsyncBaseTransport | None = None) -> tuple[bool, str]:
    """服务没有健康检查接口：取接口说明，看推演接口在不在。"""
    if not cfg.url:
        return False, f"未配置（{URL_ENV} 未设置）"
    try:
        async with httpx2.AsyncClient(timeout=min(cfg.timeout_s, 10.0), transport=transport) as http:
            resp = await http.get(f"{cfg.url}{SPEC_PATH}", headers=cfg.headers())
    except httpx2.HTTPError as err:
        return False, f"{type(err).__name__}: {err}"
    if resp.status_code != 200:
        return False, f"{resp.status_code} {resp.text[:200]}"
    try:
        spec = resp.json()
    except ValueError:
        return False, "接口说明不是 JSON"
    paths = spec.get("paths") if isinstance(spec, dict) else None
    ok = isinstance(paths, dict) and SIM_PATH in paths
    version = (spec.get("info") or {}).get("version") if isinstance(spec, dict) else None
    return ok, f"接口说明版本 {version}" + ("" if ok else f"；没有 {SIM_PATH}")
