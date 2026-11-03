#!/usr/bin/env python3
"""实验 0001：从正式的总指挥预设生成 B、C 组的叠加 patch（docs/experiments/0001-skill-vs-workflow.md）。

    python3 experiments/gen_patches.py        # 写 experiments/patches/exp-{b,c}.patch.yml

做法与验证清单 #19 相同：按 id 覆盖 preset-rescue-commander，内容取自 dsh-bundle/presets/commander.patch.yml，
只做下面的改动（文本替换，正式预设改了重新生成即可）：
  - 去掉 mine_data、fire_model、workflow 三个 MCP 客户端（技能脚本是唯一的计算路径），保留 actions，加上 kb；
  - 加上 tool-bash（主机层 bash-sandbox 管文件隔离）、tool-fs、tool-fs-search；
  - 技能目录换成 experiments/skills/<组>（加上 skills/shared）；
  - persona 中“标准流程由 workflow 服务的工具……”一段换成按技能工作的说明（PERSONA_<组>）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dsh-bundle/presets/commander.patch.yml"
OUT = ROOT / "experiments/patches"

INDENT = "      "  # 去掉 insert 一层后，预设 plugins 列表项的缩进

KB = """\
      - id: mcp-kb
        name: '@deepseek-ai/dsh-mcp-client'
        config:
          serverName: kb
          transport: streamable-http
          url: !!js "process.env.RESCUE_MCP_KB_URL ?? 'http://127.0.0.1:8105/mcp'"
          headers: !!js "process.env.RESCUE_MCP_TOKEN ? { Authorization: 'Bearer ' + process.env.RESCUE_MCP_TOKEN } : {}"
          toolCallTimeoutMs: 60000
          failOnStartupError: false
"""

TOOLS = """\
      - id: tool-bash
        name: '@deepseek-ai/dsh-tool-bash'
      - id: tool-fs
        name: '@deepseek-ai/dsh-tool-fs'
      - id: tool-fs-search
        name: '@deepseek-ai/dsh-tool-fs-search'
        config:
          sampleOverCapGlobResults: false
"""

# 第 2 步再定 persona；第 0 步（环境探测）先保留原文
PERSONA: dict[str, str | None] = {"b": None, "c": None}

REMOVE = ("mcp-mine-data", "mcp-fire-model", "mcp-workflow")


def blocks(text: str) -> list[str]:
    """按预设 plugins 的列表项切块（第一块是列表之前的头部）。"""
    parts = re.split(rf"(?m)^(?={INDENT}- id: )", text)
    return parts


def build(group: str) -> str:
    src = SRC.read_text(encoding="utf-8")
    _, body = src.split("- insert:\n", 1)
    text = "\n".join(line[4:] if line.startswith("    ") else line for line in body.splitlines()) + "\n"
    assert text.startswith("- id: preset-rescue-commander"), "正式预设的结构变了，检查生成脚本"

    out = []
    for b in blocks(text):
        m = re.match(rf"{INDENT}- id: (\S+)", b)
        bid = m.group(1) if m else None
        if bid in REMOVE:
            continue
        if bid == "skill-filesystem":
            b, n = re.subn(
                r"customSkillDirs: .*",
                f'customSkillDirs: !!js "[`${{process.env.RESCUE_HOME}}/experiments/skills/{group}`, `${{process.env.RESCUE_HOME}}/skills/shared`]"',
                b,
            )
            assert n == 1
        if bid == "compaction":
            out.append(KB + TOOLS)
        out.append(b)
    text = "".join(out)
    for rid in REMOVE:
        assert f"id: {rid}\n" not in text
    assert "id: mcp-actions\n" in text and "id: tool-bash\n" in text

    persona = PERSONA[group]
    if persona is not None:
        start = text.index("                - 标准流程由 workflow 服务的工具")
        end = text.index("                - 回答分两部分")
        text = text[:start] + persona + text[end:]

    head = (
        f"# 实验 0001 {group.upper()} 组的叠加 patch（由 experiments/gen_patches.py 生成，不要手改）。\n"
        "# 按 id 覆盖总指挥预设：去掉 mine_data、fire_model、workflow，加上 kb、bash、文件工具和实验技能目录。\n"
        f"#   DSH_HOME=.dsh-home dsh rescue --patch experiments/patches/exp-{group}.patch.yml --no-open\n"
    )
    return head + text


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for g in ("b", "c"):
        p = OUT / f"exp-{g}.patch.yml"
        p.write_text(build(g), encoding="utf-8")
        print(f"写入 {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
