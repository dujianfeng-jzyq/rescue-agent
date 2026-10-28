"""把数据契约导出为 JSON Schema，供 TypeScript 侧和真实矿井适配器开发者参考。

运行：python -m rescue_contract.export_schema [输出目录]
CI 会检查导出结果与仓库中的文件一致，修改模型后记得重新导出。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import models

EXPORTED = [
    "MineNetwork",
    "MineClock",
    "SensorPoint",
    "Reading",
    "PersonLocation",
    "ResourceItem",
    "RescueTeam",
    "MineEvent",
    "Plan",
    "WarningNotice",
    "DispatchOrder",
    "EvacuationNotice",
    "Escalation",
]


def render() -> dict[str, str]:
    out = {}
    for name in EXPORTED:
        schema = getattr(models, name).model_json_schema()
        schema["$comment"] = f"rescue-contract {models.CONTRACT_VERSION}"
        out[f"{name}.schema.json"] = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    return out


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[2] / "schema"
    target.mkdir(parents=True, exist_ok=True)
    for filename, text in render().items():
        (target / filename).write_text(text, encoding="utf-8")
    print(f"exported {len(EXPORTED)} schemas to {target}")


if __name__ == "__main__":
    main()
