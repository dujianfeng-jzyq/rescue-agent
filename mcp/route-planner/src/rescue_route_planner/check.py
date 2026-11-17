"""提供方接口一致性检查：发现提供方服务的接口或计算结果变化（docs/design-v6-route-service.md 附录 A.3）。

    uv run python -m rescue_route_planner.check http://192.168.0.200:8080 [--token <令牌>]

固定用例（2026-11-17 对提供方服务实测的结果，第 5 节）：
- GET /healthz 返回 200；
- 提供方示例输入 input.json（起点 3、终点 9）返回路线 [3,6,5,4,8,7,9]，三项指标与当时一致（相对误差 0.5% 以内）；
- 风速超出范围返回 400；终点不可达返回 200 和空 routes；
- 经适配层（我方编号）调用同一示例，结果与直接调用一致。
另有“提示”项：服务行为变化但不一定是坏事（如开始支持 length 字段、开始返回 version），只打印、不算失败。
退出码：全部通过 0，否则 1。不进 CI（需要访问提供方服务）。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from importlib import resources
from typing import Any

import httpx2

from .adapter import Edge, Node, PlanInput
from .service import HEALTH_PATH, ROUTES_PATH, Config, plan

EXPECTED_PATH = [3, 6, 5, 4, 8, 7, 9]
EXPECTED_METRICS = {"effectiveDistance": 1465.49, "smokeTime": 236.19, "temporaryEscapeTime": 600.84}
TOLERANCE = 0.005


def sample() -> dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath("fixtures/provider_input.json").read_text(encoding="utf-8"))


def to_plan_input(raw: dict[str, Any]) -> PlanInput:
    """把提供方格式的输入换成我方编号（节点 N<下标>、巷道 E<下标>），用于检查适配层的往返转换。"""
    return PlanInput(
        nodes=[Node(id=f"N{i}", xyz=xyz) for i, xyz in enumerate(raw["nodes"])],
        edges=[
            Edge(id=f"E{i}", source=f"N{e['source']}", target=f"N{e['target']}", height=e["height"], width=e["width"], wind_speed=e["windSpeed"], smoke=e["smokeConcentration"])
            for i, e in enumerate(raw["edges"])
        ],
        start=f"N{raw['start']}", targets=[f"N{raw['target']}"], walk_speed=raw["walkSpeed"], max_smoke_time=raw["maxSmokeTime"],
    )  # fmt: skip


def close(a: float, b: float) -> bool:
    return abs(a - b) <= TOLERANCE * max(abs(b), 1.0)


async def run(cfg: Config) -> list[tuple[str, bool | None, str]]:
    """返回 (检查项, 结果, 说明)；结果 None 表示“提示”（不算失败）。"""
    out: list[tuple[str, bool | None, str]] = []
    url = cfg.url
    async with httpx2.AsyncClient(timeout=cfg.timeout_s, headers=cfg.headers()) as http:

        async def post(body: dict[str, Any]) -> tuple[int, Any]:
            r = await http.post(f"{url}{ROUTES_PATH}", json=body)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, r.text

        r = await http.get(f"{url}{HEALTH_PATH}")
        out.append(("GET /healthz 返回 200", r.status_code == 200, f"{r.status_code} {r.text[:80]}"))

        base = sample()
        status, body = await post(base)
        routes = body.get("routes") if isinstance(body, dict) else None
        first = routes[0] if routes else {}
        ok = status == 200 and first.get("path") == EXPECTED_PATH
        out.append(("示例输入的路线不变", ok, f"{status} path={first.get('path')}"))
        for key, want in EXPECTED_METRICS.items():
            got = first.get(key)
            out.append((f"示例输入的 {key} 不变", isinstance(got, int | float) and close(got, want), f"{got}（原为 {want}）"))
        if isinstance(body, dict):
            extra = sorted(set(body) - {"routes"})
            out.append(("返回里的新字段（如 version、reason）", None, "、".join(extra) or "没有"))
            out.append(("返回的路线条数", None, str(len(routes or []))))

        bad = copy.deepcopy(base)
        bad["edges"][0]["windSpeed"] = 99
        status, body = await post(bad)
        out.append(("风速越界返回 400", status == 400, f"{status} {json.dumps(body, ensure_ascii=False)[:80]}"))

        lonely = copy.deepcopy(base)
        lonely["nodes"].append([0.0, 0.0, 0.0])
        lonely["target"] = len(lonely["nodes"]) - 1
        status, body = await post(lonely)
        out.append(("终点不可达返回 200 和空 routes", status == 200 and isinstance(body, dict) and body.get("routes") == [], f"{status} {json.dumps(body, ensure_ascii=False)[:80]}"))

        unknown = copy.deepcopy(base)
        unknown["noSuchField"] = 1
        status, _ = await post(unknown)
        out.append(("未知顶层字段（希望改为 400，第 6 节第 4 条）", None, f"{status}"))

        stretched = copy.deepcopy(base)
        for e in stretched["edges"]:
            e["length"] = 10000.0
        status, body = await post(stretched)
        got = ((body.get("routes") or [{}])[0].get("effectiveDistance") if isinstance(body, dict) else None) if status == 200 else None
        honoured = isinstance(got, int | float) and not close(got, EXPECTED_METRICS["effectiveDistance"])
        out.append(("巷道 length 字段（希望支持，第 6 节第 1 条）", None, "已生效" if honoured else f"被忽略（当量距离 {got}）"))

    res = await plan(to_plan_input(base), cfg)
    best = res.get("best") or {}
    want_nodes = [f"N{i}" for i in EXPECTED_PATH]
    ok = res["available"] and best.get("nodes") == want_nodes and len(best.get("edges", [])) == len(EXPECTED_PATH) - 1
    out.append(("经适配层调用示例，结果一致", ok, f"available={res['available']} nodes={best.get('nodes')} edges={best.get('edges')} error={res.get('error')}"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提供方路径规划服务的接口一致性检查")
    ap.add_argument("url", help="服务地址，如 http://192.168.0.200:8080")
    ap.add_argument("--token")
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args(argv)
    cfg = Config(url=args.url.rstrip("/"), token=args.token, timeout_s=args.timeout)
    try:
        rows = asyncio.run(run(cfg))
    except httpx2.HTTPError as err:
        print(f"✗ 无法访问 {cfg.url}：{type(err).__name__}: {err}")
        return 1
    for name, ok, detail in rows:
        mark = "·" if ok is None else ("✓" if ok else "✗")
        print(f"{mark} {name}：{detail}")
    failed = [r for r in rows if r[1] is False]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} 项通过或为提示" if not failed else f"\n{len(failed)} 项失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
