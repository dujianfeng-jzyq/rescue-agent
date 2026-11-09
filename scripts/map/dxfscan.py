"""容错的 DXF 扫描器（只用标准库）。

LibreDWG 转出的 DXF 里，MTEXT/TEXT 的值带未转义的换行，ezdxf 的严格和 recover 模式都读不了。
这里的规则：组码行必须是整数；值行之后下一行若不是整数，就视为上一个值的续行。

只做本项目要的事：按段落（TABLES 的图层、BLOCKS 的块定义、ENTITIES 的实体）切成
(类型, [(组码, 值)...]) 的实体，不解释语义。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

_INT = re.compile(r"\s*-?\d+\s*$")


@dataclass
class Entity:
    type: str
    section: str  # TABLES / BLOCKS / ENTITIES
    block: str | None  # 所在块名（只有 BLOCKS 段有）
    tags: list[tuple[int, str]] = field(default_factory=list)

    def first(self, code: int, default: str | None = None) -> str | None:
        for c, v in self.tags:
            if c == code:
                return v
        return default

    def all(self, code: int) -> list[str]:
        return [v for c, v in self.tags if c == code]

    def num(self, code: int, default: float = 0.0) -> float:
        v = self.first(code)
        try:
            return float(v) if v is not None else default
        except ValueError:
            return default


def read_pairs(path: str | Path) -> list[tuple[int, str]]:
    pairs: list[tuple[int, str]] = []
    pending: int | None = None
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if pending is None:
                if _INT.match(line):
                    pending = int(line)
                elif pairs:  # 上一个值的续行
                    c, v = pairs[-1]
                    pairs[-1] = (c, v + "\n" + line)
            else:
                pairs.append((pending, line))
                pending = None
    return pairs


def iter_entities(path: str | Path) -> Iterator[Entity]:
    pairs = read_pairs(path)
    section: str | None = None
    block: str | None = None
    cur: Entity | None = None
    i = 0
    n = len(pairs)
    while i < n:
        code, val = pairs[i]
        if code == 0:
            v = val.strip()
            if cur is not None:
                yield cur
                cur = None
            if v == "SECTION":
                section = pairs[i + 1][1].strip() if i + 1 < n else None
                i += 2
                continue
            if v == "ENDSEC":
                section = None
                i += 1
                continue
            if v == "ENDBLK":
                block = None
                i += 1
                continue
            if section in ("TABLES", "BLOCKS", "ENTITIES"):
                cur = Entity(v, section, block)
                if section == "BLOCKS" and v == "BLOCK":
                    # 块名在后面的 2 号码，读到再填
                    j = i + 1
                    while j < n and pairs[j][0] != 0:
                        if pairs[j][0] == 2:
                            block = pairs[j][1].strip()
                            cur.block = block
                            break
                        j += 1
            i += 1
            continue
        if cur is not None:
            cur.tags.append((code, val))
        i += 1
    if cur is not None:
        yield cur


_MTEXT_CODES = re.compile(r"\\[ACcFfHhQqTtWwp][^;]*;|\\[LlOoKk]|\\P|\\~|[{}]")


_SUPER = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}


def _stack(m: re.Match[str]) -> str:
    """MTEXT 堆叠 \\S上^下; ：上标数字换成上标字符，其他情况取上部。"""
    top, sep = m.group(1), m.group(2)
    if sep == "^" and top and all(ch in _SUPER for ch in top):
        return "".join(_SUPER[ch] for ch in top)
    return top


def clean_text(s: str) -> str:
    """去掉 MTEXT 的格式码，换行统一成空格。"""
    s = re.sub(r"\\S([^;^#/\\]*)([\^#/])[^;]*;", _stack, s)
    s = s.replace("\\P", "\n").replace("\\\\", "\\")
    s = _MTEXT_CODES.sub("", s)
    s = re.sub(r"\s*\n\s*", " ", s)
    return re.sub(r"\s+", " ", s).strip()
