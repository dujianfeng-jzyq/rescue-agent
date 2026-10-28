"""从 scenarios/*.yaml 加载标准场景。场景文件格式见 scenarios/README.md。"""

from __future__ import annotations

import os
from dataclasses import fields
from datetime import datetime
from pathlib import Path

import yaml
from rescue_contract.models import Measurand

from .engine import Fault, Intervention, ProcessParams, Scenario

DEFAULT_DIR = Path(__file__).resolve().parents[4] / "scenarios"


def scenario_dir() -> Path:
    return Path(os.environ.get("RESCUE_SCENARIO_DIR", DEFAULT_DIR))


def parse(doc: dict) -> Scenario:
    allowed = {f.name for f in fields(ProcessParams)}
    unknown = set(doc.get("params", {})) - allowed
    if unknown:
        raise ValueError(f"场景 {doc.get('id')} 的 params 有未知字段：{sorted(unknown)}")
    faults = tuple(
        Fault(
            sensor_id=f["sensor"],
            kind=f["kind"],
            start_h=float(f["start_h"]),
            end_h=float(f["end_h"]) if f.get("end_h") is not None else None,
            measurand=Measurand(f["measurand"]) if f.get("measurand") else None,
            magnitude=float(f.get("magnitude", 0)),
        )
        for f in doc.get("faults", [])
    )
    script = tuple(
        Intervention(
            type=e["type"],
            at_h=float(e["at_h"]),
            params=e.get("params", {}),
            roadways=tuple(e.get("roadways", [])),
            source="script",
        )
        for e in doc.get("script", [])
    )
    start = doc["start"]
    return Scenario(
        id=doc["id"],
        name=doc["name"],
        description=doc.get("description", "").strip(),
        start=start if isinstance(start, datetime) else datetime.fromisoformat(str(start)),
        duration_h=float(doc["duration_h"]),
        speed=float(doc.get("speed", 600)),
        params=ProcessParams(**doc.get("params", {})),
        faults=faults,
        script=script,
        seed=int(doc.get("seed", 7)),
    )


def load_all(directory: Path | None = None) -> dict[str, Scenario]:
    directory = directory or scenario_dir()
    out: dict[str, Scenario] = {}
    for path in sorted(directory.glob("*.yaml")):
        sc = parse(yaml.safe_load(path.read_text(encoding="utf-8")))
        if sc.id in out:
            raise ValueError(f"场景 id 重复：{sc.id}")
        out[sc.id] = sc
    if not out:
        raise FileNotFoundError(f"{directory} 下没有场景文件")
    return out
