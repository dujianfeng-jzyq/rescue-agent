#!/usr/bin/env python3
"""模拟矿井控制台（命令行）。演示和调试时用来切换场景、调速、快进、手工施加处置、查看真实状态。

    python3 scripts/sim.py state
    python3 scripts/sim.py scenarios
    python3 scripts/sim.py reset s4-escalation
    python3 scripts/sim.py speed 3600          # 1 真实秒 = 1 模拟小时
    python3 scripts/sim.py pause | resume
    python3 scripts/sim.py advance 12          # 快进 12 模拟小时
    python3 scripts/sim.py apply inert_injection rate_m3_h=1000
    python3 scripts/sim.py normal [倍速]                 # 演示：回到常态连续运行（清空历史）
    python3 scripts/sim.py inject s4-escalation [倍速]   # 演示：把事故场景接到当前矿井时间上
    python3 scripts/sim.py continue [倍速]               # 演示：自动暂停之后继续
    python3 scripts/sim.py override SM-1201-UC CO fixed 30     # 读数设置：fixed/offset 值、ramp 值 分钟、fault dropout|stuck|spike、follow
    网页控制台：http://127.0.0.1:8101/console（输入控制令牌）

只用标准库；不经过智能体。注意：state 返回的 truth（高温点真实温度）只用于演示讲解和评测，智能体看不到。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = os.environ.get("RESCUE_SIM_CONTROL_URL", "http://127.0.0.1:8101/sim").rstrip("/")
# 控制令牌，与 rescue_contract.serving.sim_control_token 一致（本脚本只用标准库，不导入它）
TOKEN = os.environ.get("RESCUE_SIM_CONTROL_TOKEN") or os.environ.get("RESCUE_MCP_MINE_DATA_TOKEN") or os.environ.get("RESCUE_MCP_TOKEN")
AUTH = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


def call(method: str, path: str, body: dict | None = None) -> object:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **AUTH},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def parse_params(items: list[str]) -> dict:
    out: dict = {}
    for item in items:
        k, _, v = item.partition("=")
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, *rest = argv
    match cmd:
        case "state":
            r = call("GET", "/state")
        case "scenarios":
            r = call("GET", "/scenarios")
        case "reset":
            r = call("POST", "/scenario", {"id": rest[0]})
        case "speed":
            r = call("POST", "/speed", {"speed": float(rest[0])})
        case "pause":
            r = call("POST", "/pause", {"paused": True})
        case "resume":
            r = call("POST", "/pause", {"paused": False})
        case "advance":
            r = call("POST", "/advance", {"hours": float(rest[0])})
        case "normal":
            r = call("POST", "/demo/normal", {"speed": float(rest[0])} if rest else {})
        case "inject":
            r = call("POST", "/demo/inject", {"scenario": rest[0], **({"speed": float(rest[1])} if len(rest) > 1 else {})})
        case "continue":
            r = call("POST", "/demo/resume", {"speed": float(rest[0])} if rest else {})
        case "override":
            sid, meas, mode, *more = rest
            body: dict = {"sensor_id": sid, "measurand": None if meas == "all" else meas, "mode": mode}
            if mode == "fault":
                body["fault"] = more[0]
            elif more:
                body["value"] = float(more[0])
                if len(more) > 1:
                    body["minutes"] = float(more[1])
            r = call("POST", "/overrides", body)
        case "apply":
            r = call("POST", "/interventions", {"type": rest[0], "params": parse_params(rest[1:]), "source": "control"})
        case _:
            print(__doc__)
            return 1
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
