"""把数据契约导出为 JSON Schema，供 TypeScript 侧和真实矿井适配器开发者参考。

运行：python -m rescue_contract.export_schema [输出目录]
CI 会检查导出结果与仓库中的文件一致，修改模型后记得重新导出。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from . import action_policy, models
from .redlines import RULES

# 红线规则所依据的规程（RULES 的条号都指这部规程）
REDLINE_DOCUMENT = "矿山救援规程"

EXPORTED = [
    "MineNetwork",
    "MineClock",
    "SensorPoint",
    "Reading",
    "PersonLocation",
    "PersonnelSnapshot",
    "ResourceItem",
    "RescueTeam",
    "MineEvent",
    "FireArea",
    "Plan",
    "WarningNotice",
    "DispatchOrder",
    "EvacuationNotice",
    "Escalation",
    # 第二版（0.7.0）：证据插件按这些 schema 校验后写入记录；固定流程、审批插件按它们组织结构化结果
    "Record",
    "Origin",
    "ResultCard",
    "HumanInputRequest",
    "ApprovalSummary",
    "FlowEvent",
    # 第三版（0.10.0）：rescue-mine 接口的自描述、报警、设备、通知与指令
    "MineCapabilities",
    "MineDescription",
    "MineAlarm",
    "EquipmentStatus",
    "NoticeRequest",
    "DispatchRequest",
    "TreatmentRequest",
    "MineReceipt",
]


def redline_articles() -> dict:
    """红线规则表的条号（知识库插件、固定流程按它批量取条文，不在 TS 里再写一遍规则，约定 3）。"""
    rules = [
        {
            "rule_id": r.rule_id,
            "summary": r.summary,
            "articles": [f"第{n}条" for n in re.findall(r"\d+", r.article)],
            "applies_to": sorted(k.value for k in r.applies_to),
        }
        for r in RULES
    ]
    numbers = sorted({int(a[1:-1]) for r in rules for a in r["articles"]})
    return {"$comment": f"rescue-contract {models.CONTRACT_VERSION}；由 redlines.RULES 导出", "document": REDLINE_DOCUMENT, "rules": rules, "articles": [f"第{n}条" for n in numbers]}


def render() -> dict[str, str]:
    out = {"redline-articles.json": json.dumps(redline_articles(), ensure_ascii=False, indent=2) + "\n"}
    for name in EXPORTED:
        schema = getattr(models, name).model_json_schema()
        schema["$comment"] = f"rescue-contract {models.CONTRACT_VERSION}"
        out[f"{name}.schema.json"] = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    return out


# DSH bundle 运行时读取的导出（审批插件按它决定哪些工具弹确认）：放在 bundle 的 data/ 下随包发布
BUNDLE_DATA = Path(__file__).parents[4] / "dsh-bundle" / "data"


def render_bundle_data() -> dict[str, str]:
    return {"action-policy.json": json.dumps(action_policy.export(), ensure_ascii=False, indent=2) + "\n"}


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[2] / "schema"
    target.mkdir(parents=True, exist_ok=True)
    for filename, text in render().items():
        (target / filename).write_text(text, encoding="utf-8")
    if len(sys.argv) <= 1:  # 导出到默认目录时同时更新 bundle 的数据文件
        for filename, text in render_bundle_data().items():
            (BUNDLE_DATA / filename).write_text(text, encoding="utf-8")
    print(f"exported {len(EXPORTED)} schemas and redline-articles.json to {target}")


if __name__ == "__main__":
    main()
