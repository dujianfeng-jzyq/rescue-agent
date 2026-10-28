#!/usr/bin/env python3
"""离线检查 DSH bundle 的 patch 文件（不需要安装 DSH）。

检查内容：
- YAML 能解析（支持 `!!js` 标签）；
- 每个 `!!js` 表达式在 Node 中语法正确，并能用示例环境变量求值；
- 预设：id 唯一、带 persona、技能目录存在、MCP serverName 合法且在预设内唯一、
  只有总指挥协调预设挂载 actions 服务、MCP 地址来自 RESCUE_MCP_* 环境变量；
- package.json 的 dsh.bundle.patch 列出了全部 patch 文件。

用法：python3 scripts/check_patches.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "dsh-bundle"
SAMPLE_ENV = {"RESCUE_HOME": str(ROOT), "RESCUE_MCP_TOKEN": "sample-token"}
SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
errors: list[str] = []


class JsExpr(str):
    pass


def _js(loader: yaml.SafeLoader, node: yaml.Node) -> JsExpr:
    return JsExpr(loader.construct_scalar(node))


yaml.SafeLoader.add_constructor("tag:yaml.org,2002:js", _js)


def load(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"{path.name}: YAML 解析失败：{exc}")
        return []


def walk(obj, found: list[tuple[str, JsExpr]], where: str = ""):
    if isinstance(obj, JsExpr):
        found.append((where, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, found, f"{where}.{k}" if where else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, found, f"{where}[{i}]")


def eval_js(exprs: list[tuple[str, str]]) -> list:
    """在 Node 中求值全部表达式（只读 process.env，与 DSH 的 !!js 用法一致）。"""
    script = (
        "const exprs = JSON.parse(process.argv[1]);"
        "Object.assign(process.env, JSON.parse(process.argv[2]));"
        "const out = exprs.map(e => { try { return { ok: true, value: (new Function('ctx', 'return (' + e + ')'))({}) } }"
        " catch (err) { return { ok: false, error: String(err) } } });"
        "process.stdout.write(JSON.stringify(out));"
    )
    res = subprocess.run(
        ["node", "-e", script, json.dumps([e for _, e in exprs]), json.dumps(SAMPLE_ENV)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(res.stdout)


def main() -> int:
    pkg = json.loads((BUNDLE / "package.json").read_text())
    listed = {Path(p).name for p in pkg["dsh"]["bundle"]["patch"]}
    files = [BUNDLE / "cordis.patch.yml", *sorted((BUNDLE / "presets").glob("*.patch.yml"))]
    for f in files:
        if f.name not in listed:
            errors.append(f"package.json dsh.bundle.patch 没有列出 {f.relative_to(BUNDLE)}")

    # profiles/ 下的可选 patch（如本地大模型）不进 bundle，只做语法和表达式检查
    files += sorted((BUNDLE.parent / "profiles").glob("*.patch.yml"))

    exprs: list[tuple[str, str]] = []
    preset_ids: set[str] = set()
    for f in files:
        doc = load(f) or []
        walk(doc, exprs, f.name)
        for block in doc:
            for row in block.get("insert", []) if isinstance(block, dict) else []:
                if row.get("name") != "@deepseek-ai/dsh-agent-preset":
                    continue
                cfg = row["config"]
                pid = cfg["id"]
                if pid in preset_ids:
                    errors.append(f"{f.name}: 预设 id 重复 {pid}")
                preset_ids.add(pid)
                plugins = {p["id"]: p for p in cfg["plugins"]}
                if "persona" not in plugins:
                    errors.append(f"{pid}: 缺少 persona")
                servers = [p["config"]["serverName"] for p in cfg["plugins"] if p.get("name") == "@deepseek-ai/dsh-mcp-client"]
                if len(servers) != len(set(servers)):
                    errors.append(f"{pid}: MCP serverName 重复")
                for p in cfg["plugins"]:
                    if p.get("name") != "@deepseek-ai/dsh-mcp-client":
                        continue
                    c = p["config"]
                    if not SERVER_NAME.match(c["serverName"]):
                        errors.append(f"{pid}: serverName 不合法 {c['serverName']}")
                    if "RESCUE_MCP_" not in str(c.get("url", "")):
                        errors.append(f"{pid}: {c['serverName']} 的地址没有走 RESCUE_MCP_* 环境变量")
                if "actions" in servers and pid != "rescue-commander":
                    errors.append(f"{pid}: 只有 rescue-commander 可以挂载 actions 服务（发布指令）")

    results = eval_js(exprs)
    for (where, expr), r in zip(exprs, results, strict=True):
        if not r["ok"]:
            errors.append(f"{where}: !!js 求值失败：{r['error']}  表达式：{expr}")
        elif "customSkillDirs" in where:
            for d in r["value"]:
                if not Path(d).is_dir():
                    errors.append(f"{where}: 技能目录不存在 {d}")
                elif not any(Path(d).glob("*/SKILL.md")):
                    errors.append(f"{where}: 技能目录下没有 */SKILL.md：{d}")

    expected = {"rescue-commander", "rescue-assessment", "rescue-dispatch", "rescue-plan", "rescue-compliance"}
    if preset_ids != expected:
        errors.append(f"预设集合不符：{sorted(preset_ids)}")

    if errors:
        print("\n".join(f"FAIL  {e}" for e in errors))
        return 1
    print(f"OK  {len(files)} 个 patch 文件，{len(preset_ids)} 个预设，{len(exprs)} 个 !!js 表达式")
    return 0


if __name__ == "__main__":
    sys.exit(main())
