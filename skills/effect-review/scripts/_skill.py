"""技能脚本的统一入口：文件进、文件出（skills/README.md“脚本约定”）。

    python -E -s -B -X utf8 scripts/<名>.py --input-file 输入.json --output-file 结果.json [--svg-out 图.svg]

脚本函数返回 (result, params, warnings)，这里补上 algorithm_version 写成输出。
输入错误抛 ValueError：原因写到 stderr，退出码 2。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

REFERENCES = Path(__file__).resolve().parents[1] / "references"


def load_reference(name: str) -> dict[str, Any]:
    """读本技能 references/ 下的参数文件（按脚本所在目录定位，不依赖当前目录）。"""
    return yaml.safe_load((REFERENCES / name).read_text(encoding="utf-8"))


Script = Callable[[dict[str, Any], argparse.Namespace], tuple[dict[str, Any], dict[str, Any], list[str]]]


def main(version: str, fn: Script, *, svg: bool = False) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-file", required=True, type=Path)
    ap.add_argument("--output-file", required=True, type=Path)
    if svg:
        ap.add_argument("--svg-out", required=True, type=Path)
    args = ap.parse_args()
    try:
        data = json.loads(args.input_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("输入不是 JSON 对象")
        result, params, warnings = fn(data, args)
    except (ValueError, KeyError, TypeError) as err:
        print(f"输入错误：{type(err).__name__}: {err}", file=sys.stderr)
        sys.exit(2)
    out = {"algorithm_version": version, "params": params, "warnings": warnings, "result": result}
    args.output_file.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def record_result(data: dict[str, Any], name: str) -> dict[str, Any]:
    """输入里的上游结果记录（脚本输出）：取 result 部分。"""
    rec = data.get(name)
    if not isinstance(rec, dict) or not isinstance(rec.get("result"), dict):
        raise ValueError(f"输入缺少上游结果 {name}（应为技能脚本的输出，含 result）")
    return rec["result"]


def evidence(data: dict[str, Any]) -> dict[str, Any]:
    ev = data.get("evidence")
    if not isinstance(ev, dict) or "groups" not in ev or "as_of" not in ev:
        raise ValueError("输入缺少证据 evidence（证据插件或 rescue_contract.collect 按采集清单取到的内容）")
    return ev
