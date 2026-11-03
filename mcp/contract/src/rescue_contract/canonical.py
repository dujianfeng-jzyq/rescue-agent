"""记录内容的规范化 JSON（RFC 8785，JCS）和 content_sha256。

记录由证据插件（TypeScript）写入、actions（Python）重算哈希核对，所以两边的规范化必须逐字节一致：
- 对象的键按 UTF-16 码元排序，数组保持顺序，不留空白；
- 数字按 ECMAScript 的 Number→String 规则输出（与 JS 的 JSON.stringify 相同）：1.0 → "1"，1e-7 → "1e-7"，1e21 → "1e+21"；
- 字符串的转义与 JSON.stringify 相同（Python json 的 ensure_ascii=False 在这一点上一致）。
TS 侧的实现在 dsh-bundle/src/evidence/canonical.ts，两边用 tests/fixtures/canonical.json 的同一组样例测试。
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

# JS 能精确表示的整数范围；超出时两边的结果可能不同，直接拒绝
_MAX_SAFE_INT = 2**53 - 1


def _number(x: int | float) -> str:
    if isinstance(x, int):
        if abs(x) > _MAX_SAFE_INT:
            raise ValueError(f"整数 {x} 超出 JS 安全整数范围，不能规范化")
        return str(x)
    if not math.isfinite(x):
        raise ValueError(f"非有限数 {x} 不能写进 JSON")
    if x == 0:
        return "0"  # 含 -0
    if x.is_integer() and _MAX_SAFE_INT < abs(x) < 1e21:
        # JS 会把它当作超出安全范围的整数输出全部位数，读回 Python 时是 int；两边都拒绝
        raise ValueError(f"整数值 {x} 超出 JS 安全整数范围，不能规范化")
    sign = "-" if x < 0 else ""
    # repr 给出能还原的最短十进制位数，与 JS 一致；只有排版（指数写法）不同
    t = Decimal(repr(abs(x))).as_tuple()
    digits = "".join(map(str, t.digits)).rstrip("0")
    exp = t.exponent + (len(t.digits) - len(digits))
    k = len(digits)
    n = exp + k  # 小数点位置：值 = 0.digits × 10^n
    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mant = digits if k == 1 else digits[0] + "." + digits[1:]
        s = f"{mant}e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + s


def _key(k: str) -> bytes:
    return k.encode("utf-16-be", "surrogatepass")


def _dump(v: Any, out: list[str]) -> None:
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, (int, float)):
        out.append(_number(v))
    elif isinstance(v, str):
        out.append(json.dumps(v, ensure_ascii=False))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, item in enumerate(v):
            if i:
                out.append(",")
            _dump(item, out)
        out.append("]")
    elif isinstance(v, dict):
        bad = [k for k in v if not isinstance(k, str)]
        if bad:
            raise TypeError(f"对象的键必须是字符串：{bad[0]!r}")
        out.append("{")
        for i, k in enumerate(sorted(v, key=_key)):
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _dump(v[k], out)
        out.append("}")
    else:
        raise TypeError(f"不能规范化的类型：{type(v).__name__}")


def canonical_json(value: Any) -> str:
    out: list[str] = []
    _dump(value, out)
    return "".join(out)


def content_sha256(content: Any) -> str:
    """记录的 content_sha256：字符串取其 UTF-8 字节，其余取规范化 JSON 的 UTF-8 字节。"""
    data = content if isinstance(content, str) else canonical_json(content)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
