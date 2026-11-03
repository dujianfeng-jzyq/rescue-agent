"""报告模板：技能 templates/ 下的 Markdown + 占位符（Mustache 的一个子集）。

证据插件的 renderReport（dsh-bundle/src/evidence/template.ts）实现同一规则，两边对同一数据渲染结果逐字节相同
（共用样例 tests/fixtures/template-cases.json；技能的模板样例由 skills check 运行）。规则：

- ``{{a.b}}``：取值。第一段从内到外在上下文栈里找，后面逐级取下去；``{{.}}`` 是当前项。键不存在时报错（模板写错了，不悄悄留空）。
  输出：null → “缺失”，true → “是”，false → “否”，数字按 ECMAScript 的写法（与规范化 JSON 相同，不取整），
  字符串原样；对象和列表报错。不做 HTML 转义。
- ``{{#a}}…{{/a}}``：列表逐项渲染（空列表不渲染）；对象渲染一次并压入上下文；其他真值渲染一次；null、false、空字符串不渲染。
- ``{{^a}}…{{/a}}``：值为 null、false、空字符串或空列表时渲染一次。
- ``{{! 注释 }}``：不输出。
- 只有一个区块标签（# ^ / !）的行整行去掉（含换行），模板可以按行排版。
"""

from __future__ import annotations

import re
from typing import Any

from .canonical import _number

_TAG = re.compile(r"\{\{\s*([#^/!]?)\s*(.*?)\s*\}\}", re.S)
_STANDALONE = re.compile(r"^[ \t]*\{\{\s*[#^/!][^{}]*\}\}[ \t]*\r?\n?$")


class TemplateError(ValueError):
    pass


def _strip_standalone(text: str) -> str:
    lines = re.split(r"(?<=\n)", text)  # 只按 \n 分行（与 TS 一致；splitlines 还会按 \r、\u2028 等分）
    return "".join(re.sub(r"^[ \t]*(\{\{.*\}\})[ \t]*\r?\n?$", r"\1", ln, flags=re.S) if _STANDALONE.match(ln) else ln for ln in lines)


def _parse(text: str) -> list[Any]:
    root: list[Any] = []
    stack: list[tuple[str, list[Any]]] = [("", root)]
    pos = 0
    for m in _TAG.finditer(text):
        if m.start() > pos:
            stack[-1][1].append(text[pos : m.start()])
        pos = m.end()
        kind, name = m.group(1), m.group(2)
        if kind == "!":
            continue
        if not name:
            raise TemplateError(f"空标签：{m.group(0)}")
        if kind in ("#", "^"):
            node: dict[str, Any] = {"section": name, "inverted": kind == "^", "children": []}
            stack[-1][1].append(node)
            stack.append((name, node["children"]))
        elif kind == "/":
            if len(stack) == 1 or stack[-1][0] != name:
                raise TemplateError(f"区块结束标签 {{{{/{name}}}}} 没有对应的开始标签")
            stack.pop()
        else:
            stack[-1][1].append({"var": name})
    if len(stack) > 1:
        raise TemplateError(f"区块 {{{{#{stack[-1][0]}}}}} 没有结束标签")
    if pos < len(text):
        root.append(text[pos:])
    return root


_MISSING = object()


def _lookup(path: str, ctx: list[Any]) -> Any:
    if path == ".":
        return ctx[-1]
    first, *rest = path.split(".")
    value: Any = _MISSING
    for frame in reversed(ctx):
        if isinstance(frame, dict) and first in frame:
            value = frame[first]
            break
    for key in rest:
        if value is _MISSING:
            break
        value = value[key] if isinstance(value, dict) and key in value else _MISSING
    if value is _MISSING:
        raise TemplateError(f"模板变量不存在：{path}")
    return value


def _falsy(v: Any) -> bool:
    return v is None or v is False or v == "" or (isinstance(v, list) and not v)


def _text(v: Any, path: str) -> str:
    if v is None:
        return "缺失"
    if v is True:
        return "是"
    if v is False:
        return "否"
    if isinstance(v, int | float):
        return _number(v)
    if isinstance(v, str):
        return v
    raise TemplateError(f"模板变量 {path} 是{'列表' if isinstance(v, list) else '对象'}，不能直接输出")


def _render(nodes: list[Any], ctx: list[Any], out: list[str]) -> None:
    for n in nodes:
        if isinstance(n, str):
            out.append(n)
        elif "var" in n:
            out.append(_text(_lookup(n["var"], ctx), n["var"]))
        else:
            v = _lookup(n["section"], ctx)
            if n["inverted"]:
                if _falsy(v):
                    _render(n["children"], ctx, out)
            elif isinstance(v, list):
                for item in v:
                    _render(n["children"], [*ctx, item], out)
            elif not _falsy(v):
                _render(n["children"], [*ctx, v], out)


def render(template: str, data: dict[str, Any]) -> str:
    out: list[str] = []
    _render(_parse(_strip_standalone(template)), [data], out)
    return "".join(out)


def report_data(records: dict[str, dict[str, Any]], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """renderReport 交给模板的数据（与 dsh-bundle/src/evidence/service.ts 的 renderReport 相同）：
    {panel, as_of, as_of_text, record_ids: {名: 编号}, <名>: 记录内容, ...extra}。panel、as_of 取第一个记录的。
    records 的值是记录（含 record_id、panel、as_of、content）。"""
    first = next(iter(records.values()))
    data: dict[str, Any] = {
        "panel": first.get("panel"),
        "as_of": first["as_of"],
        "as_of_text": first["as_of"][:19].replace("T", " "),
        "record_ids": {k: r["record_id"] for k, r in records.items()},
    }
    for k, r in records.items():
        data[k] = r["content"]
    return data | (extra or {})
