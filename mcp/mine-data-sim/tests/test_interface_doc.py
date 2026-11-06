"""接口说明由代码生成：文档和代码不一致就失败（改了工具或模型后运行 make interface-doc）。"""

import asyncio
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _generate() -> str:
    spec = importlib.util.spec_from_file_location("make_interface_doc", ROOT / "scripts" / "make_interface_doc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return asyncio.run(mod.generate())


def test_doc_is_up_to_date():
    doc = ROOT / "docs" / "rescue-mine-interface.md"
    assert doc.is_file(), "先运行 make interface-doc 生成 docs/rescue-mine-interface.md"
    assert doc.read_text(encoding="utf-8") == _generate(), "docs/rescue-mine-interface.md 与代码不一致，请运行 make interface-doc"


def test_doc_covers_every_tool_and_role():
    from rescue_contract.interface import ALL_TOOLS
    from rescue_contract.models import SensorRole

    text = _generate()
    for tool in ALL_TOOLS:
        assert f"#### `{tool}`" in text, tool
    for role in SensorRole:
        assert f"| `{role.value}` |" in text, role
    assert "写入令牌" in text and "idempotency_key" in text
