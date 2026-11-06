#!/usr/bin/env python3
"""用模拟矿井生成 docs/rescue-mine-interface.md（接口说明由代码生成，见 rescue_contract.interface_doc）。

    uv run --project mcp python scripts/make_interface_doc.py          # 写文件
    uv run --project mcp python scripts/make_interface_doc.py --check  # 只检查文件是否最新（CI）
"""

import asyncio
import sys
from pathlib import Path

from rescue_contract.client import connect
from rescue_contract.interface_doc import render
from rescue_mine_data_sim.server import SimHolder, build_server

DOC = Path(__file__).resolve().parents[1] / "docs" / "rescue-mine-interface.md"


async def generate() -> str:
    async with connect(build_server(SimHolder("s1-normal", manual_clock=True))) as c:
        return render((await c.list_tools()).tools)


def main() -> int:
    text = asyncio.run(generate())
    if "--check" in sys.argv:
        if not DOC.is_file() or DOC.read_text(encoding="utf-8") != text:
            print(f"{DOC} 与代码不一致，请运行 make interface-doc 重新生成", file=sys.stderr)
            return 1
        print("接口说明是最新的")
        return 0
    DOC.write_text(text, encoding="utf-8")
    print(f"已生成 {DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
