"""提供方接口一致性检查：发现提供方火灾推演服务的接口或计算结果变化（docs/design-v7-fire-sim.md 附录 A.3）。

    uv run python -m rescue_fire_sim.check http://192.168.0.200:5079 [--token <令牌>]

固定用例（2026-11-17 对提供方服务实测的结果，第 5 节）：
- 接口说明（/swagger/v1/swagger.json）里有推演接口；
- 提供方示例输入返回 200，各巷道首次出现 CO 的时刻和 CO 峰值与当时一致；
- 燃料类型越界、火源巷道不存在返回 400；
- 经适配层（我方编号）调用同一示例，结果与直接调用一致。
另有“提示”项：第 6 节提的问题是否有变化（原始返回摘要、火源在 0 号巷道的 500、火对风速的影响、未知字段、版本号），
只打印、不算失败。退出码：全部通过 0，否则 1。不进 CI（需要访问提供方服务）。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import sys
from importlib import resources
from typing import Any

import httpx2

from .adapter import Boundary, Branch, Fire, Node, SimInput
from .service import SIM_PATH, Config, health, simulate

INV_MARKERS = {0: None, 1: "inlet", 2: "outlet"}
INV_MODES = {1: "forced", 2: "exhaust"}


def _fixture(name: str) -> dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath(f"fixtures/{name}").read_text(encoding="utf-8"))


def sample() -> dict[str, Any]:
    return _fixture("provider_input.json")


def expected() -> dict[str, Any]:
    return _fixture("provider_expected.json")


def to_sim_input(raw: dict[str, Any]) -> SimInput:
    """把提供方格式的输入换成我方编号（节点 N<下标>、巷道 B<下标>），用于检查适配层的往返转换。"""
    fs, vb = raw["fireSource"], raw["ventBoundary"]
    return SimInput(
        nodes=[Node(id=f"N{n['index']}", xyz=[n["x"], n["y"], n["z"]]) for n in raw["nodes"]],
        branches=[
            Branch(
                id=f"B{b['index']}", from_node=f"N{b['from']}", to_node=f"N{b['to']}", resistance=b["resistance"], area_m2=b["area"],
                closed=b["closed"], fixed_flow_m3_s=b["fixedFlow"], boundary=INV_MARKERS[b["boundaryMarker"]],
            )
            for b in raw["branches"]
        ],
        fire=Fire(
            branch=f"B{fs['branchId']}", position=fs["position"], total_time_s=fs["totalTime"], output_interval_s=fs["outputInterval"],
            fuel_type=fs["fuelType"], burning_area_m2=fs["burningArea"], fuel_load_kg=fs["fuelLoad"], oxygen_pct=fs["oxygenConcentration"],
        ),
        boundary=Boundary(
            mode=INV_MODES[vb["mode"]],
            inlet_flows_m3_s={f"N{k}": v for k, v in (vb.get("inletFlows") or {}).items()},
            outlet_pressures_pa={f"N{k}": v for k, v in (vb.get("outletPressures") or {}).items()},
        ),
    )  # fmt: skip


def co_digest(body: Any) -> dict[str, list[float]]:
    """提供方原始返回 → {巷道下标: [首次出现 CO 的时刻, 峰值 ppm]}（只列有 CO 的巷道）。"""
    out: dict[str, list[float]] = {}
    for g in body["coConcentration"]["branches"]:
        first, peak = None, 0.0
        for r in g["rows"]:
            v = r["values"]
            m = v if isinstance(v, int | float) else max(v)
            if m > 0 and first is None:
                first = r["time"]
            peak = max(peak, m)
        if peak > 0:
            out[str(g["branchId"])] = [first, round(peak * 10_000, 1)]
    return out


def max_speed_change(body: Any) -> float:
    rows = body["windSpeed"]["rows"]
    return max(abs(b - a) for a, b in zip(rows[0]["values"], rows[-1]["values"], strict=True))


async def run(cfg: Config) -> list[tuple[str, bool | None, str]]:
    """返回 (检查项, 结果, 说明)；结果 None 表示“提示”（不算失败）。"""
    out: list[tuple[str, bool | None, str]] = []
    ok, detail = await health(cfg)
    out.append(("接口说明里有推演接口", ok, detail))
    want = expected()
    async with httpx2.AsyncClient(timeout=cfg.timeout_s, headers=cfg.headers()) as http:

        async def post(body: dict[str, Any]) -> tuple[int, Any, bytes]:
            r = await http.post(f"{cfg.url}{SIM_PATH}", json=body)
            try:
                return r.status_code, r.json(), r.content
            except ValueError:
                return r.status_code, r.text, r.content

        base = sample()
        status, body, raw = await post(base)
        if status == 200 and isinstance(body, dict):
            got = co_digest(body)
            same = got == want["co"]
            diff = sorted(k for k in set(got) | set(want["co"]) if got.get(k) != want["co"].get(k))
            out.append(("示例输入的 CO 首现时刻和峰值不变", same, "一致" if same else f"有变化的巷道下标：{'、'.join(diff)}"))
            out.append(("示例输入的帧数不变", len(body["windSpeed"]["rows"]) == want["frames"], str(len(body["windSpeed"]["rows"]))))
            sha = hashlib.sha256(raw).hexdigest()
            out.append(
                ("原始返回逐字节不变（服务确定性、无格式变化）", None, "不变" if sha == want["result_sha256"] else f"变了：{sha[:16]}…")
            )
            extra = sorted(set(body) - {"windSpeed", "coConcentration"})
            out.append(("返回里的新字段（如 version、O2、温度、烟雾）", None, "、".join(extra) or "没有"))
        else:
            out.append(("示例输入的 CO 首现时刻和峰值不变", False, f"{status} {str(body)[:120]}"))

        bad = copy.deepcopy(base)
        bad["fireSource"]["fuelType"] = 9
        status, body, _ = await post(bad)
        out.append(("燃料类型越界返回 400", status == 400, f"{status} {json.dumps(body, ensure_ascii=False)[:80]}"))

        bad = copy.deepcopy(base)
        bad["fireSource"]["branchId"] = 999
        status, body, _ = await post(bad)
        out.append(("火源巷道不存在返回 400", status == 400, f"{status} {json.dumps(body, ensure_ascii=False)[:80]}"))

        b0 = copy.deepcopy(base)
        b0["fireSource"].update(branchId=0, position=0.5)
        status, body, _ = await post(b0)
        out.append(
            (
                "火源放在 0 号巷道（第 6 节第 4 条，原为 500）",
                None,
                f"{status} {json.dumps(body, ensure_ascii=False)[:80]}" if status != 200 else "已能计算",
            )
        )

        big = copy.deepcopy(base)
        big["fireSource"].update(fuelLoad=5000, burningArea=300)
        status, body, _ = await post(big)
        dv = max_speed_change(body) if status == 200 and isinstance(body, dict) else None
        out.append(
            (
                "大火（5000 kg、300 m²）对风速的最大影响（第 6 节第 1 条，原为 0.012 m/s）",
                None,
                f"{round(dv, 3)} m/s" if dv is not None else f"{status}",
            )
        )

        unknown = copy.deepcopy(base)
        unknown["noSuchField"] = 1
        status, _, _ = await post(unknown)
        out.append(("未知顶层字段（希望改为 400，第 6 节第 6 条）", None, f"{status}"))

    res = await simulate(to_sim_input(base), cfg)
    got = {
        b["id"][1:]: [b["co_first_s"], b["co_peak_ppm"]] for b in res["branches"] if b["co_peak_ppm"] > 0
    }  # fmt: skip
    ok = res["available"] and got == want["co"] and res["result_sha256"] is not None
    out.append(("经适配层调用示例，结果一致", ok, f"available={res['available']} error={res.get('error')} 有 CO 的巷道 {len(got)} 条"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提供方火灾推演服务的接口一致性检查")
    ap.add_argument("url", help="服务地址，如 http://192.168.0.200:5079")
    ap.add_argument("--token")
    ap.add_argument("--timeout", type=float, default=60.0)
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
