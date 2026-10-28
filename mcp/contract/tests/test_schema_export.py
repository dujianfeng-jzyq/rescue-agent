"""数据契约导出的 JSON Schema 必须与仓库中的文件一致，避免两边不同步。"""

from pathlib import Path

from rescue_contract.export_schema import render

SCHEMA_DIR = Path(__file__).parents[1] / "schema"


def test_exported_schemas_are_up_to_date():
    for filename, text in render().items():
        path = SCHEMA_DIR / filename
        assert path.exists(), f"缺少 {filename}，请运行 python -m rescue_contract.export_schema"
        assert path.read_text(encoding="utf-8") == text, f"{filename} 已过期，请运行 python -m rescue_contract.export_schema"
