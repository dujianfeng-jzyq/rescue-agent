"""报告模板（rescue_contract.template）：共用样例与证据插件的 template.ts 逐字节一致。"""

import json
from pathlib import Path

import pytest
from rescue_contract.template import TemplateError, render

CASES = json.loads((Path(__file__).parent / "fixtures" / "template-cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_cases(case):
    if "error" in case:
        with pytest.raises(TemplateError, match=case["error"]):
            render(case["template"], case["data"])
    else:
        assert render(case["template"], case["data"]) == case["expected"]
