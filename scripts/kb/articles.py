"""条文切分规则（离线入库用，docs/design-v2.md 4.7）：把法规、规程类文档的全文按“章—节—条—款—项”切开。

输入是 RAGFlow laws 切法的切片（按页面位置排好序）。要处理的版面问题：
- 目录：带连续点号的行（“第一章总则....6”）；
- 切片开头重复的章节路径（laws 切法给每个切片加上所在章节）：与当前路径相同的章节标题行不切断条文；
- PDF 换行：句中断行（“金属导\\n体”）要接上；汉字之间因换行留下的空格（“风 量”）要去掉；
- 正文里引用别的条（“依照第七十五条规定”）、行首断行带出的引用（“第八十五条规定的……”）不是条文标题：
  条文标题必须是行首的“第X条”，且条号按顺序递增（只接受当前条号 +1），这样跨切片的条文也能完整接上。

只认“第X条”结构的文档（法律、条例、规程、细则、办法、规定）；标准（GB、MT、AQ、KA/T 的 4.1.2 编号）不切，只走检索。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN = "零〇一二两三四五六七八九十百千"


def cn_to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    total, cur = 0, None
    for ch in s:
        if ch in CN_DIGITS:
            cur = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            total += (1 if cur is None else cur) * CN_UNITS[ch]
            cur = None
        else:
            return None
    return total + (cur or 0)


# 行首“第X条”后紧跟“规定”“的”或标点的是断行带出的引用（“第八十五条规定的……”）；其余靠条号连续来排除
# （“第十三条所有……”“第二十四条中国……”是条文标题，不能按后面的字排除）
ARTICLE_HEAD = re.compile(rf"^\s*第\s*([0-9]+|[{_CN}]+)\s*条(?!规定|的|[、，,；;。])\s*")
CHAPTER = re.compile(rf"^\s*第\s*([{_CN}0-9]+)\s*章\s*(\S.*?)?\s*$")
PART = re.compile(rf"^\s*(总\s*则|附\s*则|第\s*[{_CN}0-9]+\s*编(\s*\S.*)?)\s*$")  # 不带“第X章”的章名、编名
SECTION = re.compile(rf"^\s*第\s*([{_CN}0-9]+)\s*节\s*(\S.*?)?\s*$")
APPENDIX = re.compile(r"^\s*(附\s*录|附\s*件|附\s*表)")  # 正文结束：附录、附件不属于最后一条
TOC = re.compile(r"(\.{2,}|…{1,}|·{3,})\s*\d*\s*$|^\s*\.*\s*\d+\s*$")
ITEM = re.compile(rf"^\s*[（(]([{_CN}0-9]+)[）)]")
# 句末标点：换行前是这些时保留换行（款、项的分界），否则视为 PDF 断行
SENTENCE_END = tuple("。；：:;！？!?」”）)")
CJK = r"[㐀-鿿　-〿＀-￯]"


def clean_spaces(text: str) -> str:
    """去掉汉字（含全角标点）与汉字、数字之间因 PDF 排版留下的空白（“风 量”“45 天”）；字母之间的空白保留（“GB 50016”）。"""
    text = re.sub(rf"(?<={CJK})[ \t\u3000]+(?={CJK}|\d)", "", text)
    return re.sub(rf"(?<=\d)[ \t\u3000]+(?={CJK})", "", text)


@dataclass
class Article:
    number: int
    heading: str  # 原文条号写法，如“第八十五条”
    chapter: str | None
    section: str | None
    lines: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)

    def paragraphs(self) -> list[str]:
        """按换行切出的段：第一段含条号后的正文；（一）（二）…各为一段。"""
        return [ln for ln in self.lines if ln]

    def to_dict(self) -> dict:
        paras = self.paragraphs()
        items = []
        for p in paras:
            m = ITEM.match(p)
            if m:
                items.append({"label": m.group(0).strip(), "number": cn_to_int(m.group(1)), "text": p})
        return {
            "number": self.number,
            "article": f"第{self.number}条",
            "heading": self.heading,
            "chapter": self.chapter,
            "section": self.section,
            "text": "\n".join(paras),
            "paragraphs": paras,
            "items": items,
            "chunk_ids": self.chunk_ids,
        }


def _lines(chunks: list[dict]) -> list[tuple[str, str]]:
    """（行, 切片 id），去掉目录行和孤立页码。"""
    out = []
    for c in chunks:
        for raw in (c.get("content") or "").splitlines():
            ln = raw.strip()
            if not ln or TOC.search(ln):
                continue
            out.append((ln, c.get("id") or ""))
    return out


def _first_article(chunk: dict) -> int | None:
    for raw in (chunk.get("content") or "").splitlines():
        m = ARTICLE_HEAD.match(raw.strip())
        if m:
            return cn_to_int(m.group(1))
    return None


def order_chunks(chunks: list[dict]) -> list[dict]:
    """laws 切法的切片顺序不一定是正文顺序（同一节里的切片按节的起始位置排，后面的条可能排在前面）。
    把含条文标题的切片和紧随其后、不含标题的切片（跨切片的续文）分成一组，按组内第一条的条号排序；
    第一条之前的切片（封面、目录）保持原位。"""
    groups: list[tuple[float, list[dict]]] = []
    for c in chunks:
        n = _first_article(c)
        if n is not None or not groups:
            groups.append((float(n) if n is not None else -1.0, [c]))
        else:
            groups[-1][1].append(c)
    groups.sort(key=lambda g: g[0])  # 稳定排序：同号（不应出现）保持原顺序
    return [c for _, cs in groups for c in cs]


def split_articles(chunks: list[dict]) -> tuple[list[Article], list[str]]:
    """返回（条文列表, 问题说明）。条号必须从 1 起连续；缺号、重号写进问题说明。"""
    chunks = order_chunks(chunks)
    articles: list[Article] = []
    chapter = section = None
    cur: Article | None = None
    problems: list[str] = []
    for ln, cid in _lines(chunks):
        mc, ms = CHAPTER.match(ln) or PART.match(ln), SECTION.match(ln)
        if mc and len(ln) <= 40:
            name = clean_spaces(ln)
            if name != chapter:
                chapter, section = name, None
                cur = None if cur is None else _close(cur, articles)
            continue
        if ms and len(ln) <= 40:
            name = clean_spaces(ln)
            if name != section:
                section = name
                cur = None if cur is None else _close(cur, articles)
            continue
        if cur is not None and APPENDIX.match(ln):
            # 附录不属于最后一条；切片顺序可能乱（附录排在末几条之前），所以只结束当前条，继续找下一条
            cur = _close(cur, articles)
            continue
        ma = ARTICLE_HEAD.match(ln)
        n = cn_to_int(ma.group(1)) if ma else None
        expected = (articles[-1].number if articles else 0) + 1 if cur is None else cur.number + 1
        if ma and n == expected:
            if cur is not None:
                _close(cur, articles)
            body = ln[ma.end() :]
            cur = Article(n, ma.group(0).strip(), chapter, section, [body] if body else [""], [cid])
            continue
        if ma and n is not None and n > expected and cur is not None and n <= expected + 3:
            problems.append(f"第{expected}条缺失（下一条是第{n}条）")
            _close(cur, articles)
            cur = Article(n, ma.group(0).strip(), chapter, section, [ln[ma.end() :]], [cid])
            continue
        if cur is None:
            continue  # 第一条之前的标题、发文信息
        if cid not in cur.chunk_ids:
            cur.chunk_ids.append(cid)
        # 新的一项、或上一行以句末标点结束 → 新起一段；否则是 PDF 断行，接到上一行
        if ITEM.match(ln) or not cur.lines[-1] or cur.lines[-1].endswith(SENTENCE_END):
            cur.lines.append(ln)
        else:
            cur.lines[-1] += ln
    if cur is not None:
        _close(cur, articles)
    seen = [a.number for a in articles]
    if seen and seen != list(range(seen[0], seen[0] + len(seen))):
        problems.append("条号不连续")
    return articles, problems


def _close(a: Article, out: list[Article]) -> None:
    a.lines = [clean_spaces(x).strip() for x in a.lines]
    out.append(a)
    return None


def is_article_document(chunks: list[dict]) -> bool:
    """是否按“第X条”组织：行首“第一条”“第二条”都出现。"""
    heads = {cn_to_int(m.group(1)) for ln, _ in _lines(chunks) if (m := ARTICLE_HEAD.match(ln))}
    return {1, 2, 3} <= heads


def document_name(file_name: str) -> str:
    """文档名去掉扩展名，作为条文索引和引用里的文档名（如“矿山救援规程”）。"""
    return re.sub(r"\.(pdf|docx?|txt|md)$", "", file_name, flags=re.I)
