"""条文索引的切分规则（scripts/kb/articles.py，docs/design-v2.md 4.7）。用构造的 laws 切法切片覆盖各种版面问题。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "kb"))

import articles as A  # noqa: E402


def chunk(cid: str, text: str) -> dict:
    return {"id": cid, "content": text}


CHUNKS = [
    chunk("c0", "某某规程"),
    chunk("c1", "第一章总则.......\n..1\n第二章火区管理....\n..3"),  # 目录
    chunk("c2", "第一章总则\n第一条为了规范火区管理，制定本规程。\n第二条本规程适用于煤 矿。"),
    # 同一节的切片顺序乱了：第五条所在的切片排在第三、四条前面
    chunk("c5", "第二章火区管理\n第一节封闭\n第五条火区封闭后应当遵守下列规定：\n（一）所有人员立即撤出危险区；\n（二）进入检查在24 小时后进行。"),
    chunk("c3", "第二章火区管理\n第一节封闭\n第三条封闭火区应当遵守下列规定:\n（一）先封闭支巷，后封闭主巷；\n（二）火区主要进风巷和回风巷中的密闭留有通风孔，其\n他密闭可以不留通风孔；"),
    # 跨切片的续文：没有条文标题，开头重复章节路径
    chunk("c3b", "第二章火区管理\n第一节封闭\n（三）封闭有爆炸危险火区时，先采取注入惰性气体等抑爆措施。\n第四条所有开采煤层应当确定最短自然发火期。\n第八十五条规定的情形除外。"),
    chunk("c6", "附则\n第六条本规程自2024年7月1日起施行。\n附录1基本装备\n指挥车 附有应急警报装置"),
]


def test_split_orders_joins_and_cleans():
    arts, problems = A.split_articles(CHUNKS)
    assert problems == []
    by = {a.number: a.to_dict() for a in arts}
    assert sorted(by) == [1, 2, 3, 4, 5, 6]
    assert by[2]["text"] == "本规程适用于煤矿。"  # 汉字间的排版空格去掉
    # 第三条跨两个切片，第（三）项完整接上；句中断行接回；重复的章节路径不切断条文
    assert by[3]["paragraphs"] == [
        "封闭火区应当遵守下列规定:",
        "（一）先封闭支巷，后封闭主巷；",
        "（二）火区主要进风巷和回风巷中的密闭留有通风孔，其他密闭可以不留通风孔；",
        "（三）封闭有爆炸危险火区时，先采取注入惰性气体等抑爆措施。",
    ]
    assert by[3]["chunk_ids"] == ["c3", "c3b"]
    assert [x["label"] for x in by[3]["items"]] == ["（一）", "（二）", "（三）"]
    # “第四条所有……”是标题（不能因为后面是“所”就排除）；“第八十五条规定的情形”是行首断行带出的引用
    assert by[4]["text"] == "所有开采煤层应当确定最短自然发火期。\n第八十五条规定的情形除外。"
    assert by[5]["paragraphs"][-1] == "（二）进入检查在24小时后进行。"  # 数字与汉字间的空格去掉
    assert (by[5]["chapter"], by[5]["section"]) == ("第二章火区管理", "第一节封闭")
    # 附录不属于最后一条；“附则”是章名
    assert by[6]["text"] == "本规程自2024年7月1日起施行。"
    assert by[6]["chapter"] == "附则"


def test_gap_is_reported():
    chunks = [chunk("a", "第一条甲。\n第二条乙。\n第四条丁。")]
    arts, problems = A.split_articles(chunks)
    assert [a.number for a in arts] == [1, 2, 4]
    assert problems and "第3条缺失" in problems[0]


def test_reference_mid_text_is_not_a_heading():
    """正文里的“依照第七十五条规定”不在行首；条号不连续的行首“第X条”也不当标题（超出 +3 的跳号视为引用）。"""
    chunks = [chunk("a", "第一条依照第七十五条规定执行。\n第九十条的规定同样适用。\n第二条乙。")]
    arts, _ = A.split_articles(chunks)
    assert [a.number for a in arts] == [1, 2]
    assert "第九十条的规定同样适用。" in arts[0].to_dict()["text"]


def test_standards_are_not_article_documents():
    assert not A.is_article_document([chunk("a", "4.1 一般规定\n4.1.1 密闭墙应当……\n4.1.2 ……")])
    assert A.is_article_document([chunk("a", "第一条甲。\n第二条乙。\n第三条丙。")])


def test_cn_numbers():
    assert [A.cn_to_int(x) for x in ("十五", "一百零一", "一百一十", "七百七十七", "12")] == [15, 101, 110, 777, 12]
    assert A.document_name("矿山救援规程.pdf") == "矿山救援规程"
