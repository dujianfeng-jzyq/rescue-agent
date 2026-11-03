"""规范化 JSON 与 content_sha256：和 TS 侧（dsh-bundle/src/evidence/canonical.ts）用同一组样例。

样例 tests/fixtures/canonical.json 由 JS 原生 JSON.stringify 生成（ECMAScript 数字规则是基准），
TS 侧的测试读同一个文件。改规范化逻辑时两边一起改、一起跑。
"""

import json
from pathlib import Path

import pytest
from rescue_contract.canonical import canonical_json, content_sha256

CASES = json.loads((Path(__file__).parent / "fixtures" / "canonical.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_matches_javascript(case):
    if isinstance(case["value"], str):
        assert case["value"] == case["canonical"]
    else:
        assert canonical_json(case["value"]) == case["canonical"]
    assert content_sha256(case["value"]) == case["sha256"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "1"),  # Python 侧算出来的整数值浮点数，JS 写成整数
        (100.0, "100"),
        (-0.0, "0"),
        (46.70, "46.7"),
        (1e-6, "0.000001"),
        (1e-7, "1e-7"),
        (123456789012.5, "123456789012.5"),
        (1e21, "1e+21"),
        (2**53 - 1, "9007199254740991"),
    ],
)
def test_python_numbers(value, expected):
    assert canonical_json(value) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 2**53, 1e20, {1: "x"}, {1, 2}])
def test_rejects(value):
    with pytest.raises((ValueError, TypeError)):
        canonical_json(value)


def test_tuple_like_list_and_bool_not_number():
    assert canonical_json({"a": (1, True, None)}) == '{"a":[1,true,null]}'
