"""guard 测试的共用夹具：模拟矿井（进程内）+ 记录库 + guard 服务。

记录用技能样例（s4-escalation 第 60 小时的封闭方案与合规审核）和手工构造的结果记录，不依赖 DSH。
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from rescue_actions.server import build_server
from rescue_contract.canonical import content_sha256
from rescue_contract.models import Origin, Record, RecordKind
from rescue_contract.records import RecordStore, new_record_id
from rescue_mine_data_sim.server import SimHolder
from rescue_mine_data_sim.server import build_server as build_mine

SKILLS = Path(__file__).resolve().parents[3] / "skills"
COMPOSE = json.loads((SKILLS / "sealing-plan/tests/cases/compose/s4-escalation-60h-full.expected.json").read_text(encoding="utf-8"))
REVIEW = json.loads((SKILLS / "plan-compliance/tests/cases/review/pass.expected.json").read_text(encoding="utf-8"))
FF_COMPOSE = json.loads((SKILLS / "firefighting-plan/tests/cases/compose/s3-accelerating-39h-full.expected.json").read_text(encoding="utf-8"))
FF_REVIEW = json.loads((SKILLS / "plan-compliance/tests/cases/review/firefighting-pass.expected.json").read_text(encoding="utf-8"))
UNSEAL_READY = json.loads((SKILLS / "unsealing-plan/tests/cases/unsealing_plan/s5-336h-ready.expected.json").read_text(encoding="utf-8"))
ORIGIN = Origin(channel="web", trigger="chat")


@dataclass
class World:
    holder: SimHolder
    mine: Any
    records: RecordStore
    ledger: Path
    server: Any

    def call(self, tool: str, args: dict[str, Any]):
        async def go():
            async with Client(self.server) as c:
                return await c.call_tool(tool, args)

        return asyncio.run(go())

    def rows(self) -> list[dict[str, Any]]:
        return [json.loads(x) for x in self.ledger.read_text(encoding="utf-8").splitlines()] if self.ledger.exists() else []

    def put(self, skill: str, script: str, content: dict[str, Any], *, inputs: dict[str, Any] | None = None, as_of: datetime | None = None, panel="1201") -> str:
        at = as_of or self.holder.engine.now()
        rec = Record(
            record_id=new_record_id(RecordKind.RESULT, at), kind=RecordKind.RESULT, panel=panel, as_of=at, created_at=datetime.now(UTC),
            origin=ORIGIN, source={"skill": skill, "script": script, **({"inputs": inputs} if inputs else {})},
            content=content, content_sha256=content_sha256(content),
        )  # fmt: skip
        return self.records.put(rec).record_id

    def result_record(self, skill: str, script: str, result: dict[str, Any], **kw) -> str:
        return self.put(skill, script, {"algorithm_version": "x", "params": {}, "warnings": [], "result": result}, **kw)

    def plan_records(self, mutate=None) -> str:
        """方案记录 + 合规审核记录（通过），返回审核记录编号；mutate 可改方案（两条记录里的方案保持一致）。"""
        compose = json.loads(json.dumps(COMPOSE))
        review = json.loads(json.dumps(REVIEW))
        if mutate:
            mutate(compose["result"]["plan"])
        review["result"]["plan"] = compose["result"]["plan"]
        pid = self.put("sealing-plan", "compose", compose)
        return self.put("plan-compliance", "review", review, inputs={"plan": pid})


    def firefighting_records(self, mutate=None) -> str:
        """防灭火方案记录 + 合规审核记录（通过），返回审核记录编号；mutate 可改方案（两条记录里的方案保持一致）。"""
        compose = json.loads(json.dumps(FF_COMPOSE))
        review = json.loads(json.dumps(FF_REVIEW))
        if mutate:
            mutate(compose["result"]["plan"])
        review["result"]["plan"] = compose["result"]["plan"]
        pid = self.put("firefighting-plan", "compose", compose)
        return self.put("plan-compliance", "review", review, inputs={"plan": pid})


def make_world(tmp_path, scenario="s4-escalation", hours=60) -> World:
    holder = SimHolder(scenario, manual_clock=True)
    holder.engine.clock.advance(hours)
    mine = build_mine(holder)
    records = RecordStore(tmp_path / "records")
    ledger = tmp_path / "ledger.jsonl"
    return World(holder, mine, records, ledger, build_server(mine, ledger_path=ledger, records=records))


@pytest.fixture
def world(tmp_path) -> World:
    assert COMPOSE["result"]["as_of"] == "2026-11-04T20:00:00"  # 样例的矿井时间：s4-escalation 第 60 小时
    w = make_world(tmp_path)
    assert w.holder.engine.now().isoformat() == COMPOSE["result"]["as_of"]
    return w


@pytest.fixture
def world_s3(tmp_path) -> World:
    """s3 加速氧化第 39 小时（橙色，防灭火方案样例的时刻）。"""
    w = make_world(tmp_path, "s3-accelerating", 39)
    assert w.holder.engine.now().isoformat() == FF_COMPOSE["result"]["as_of"]
    return w


@pytest.fixture
def world_s5(tmp_path) -> World:
    """s5 封闭火区第 336 小时（启封评估样例的时刻）。"""
    w = make_world(tmp_path, "s5-sealed-fire-area", 336)
    assert w.holder.engine.now().isoformat() == UNSEAL_READY["result"]["as_of"]
    return w


@pytest.fixture
def sample_plan() -> dict[str, Any]:
    """技能样例里的封闭方案（可随意修改，每个用例一份拷贝）。"""
    return json.loads(json.dumps(COMPOSE["result"]["plan"]))
