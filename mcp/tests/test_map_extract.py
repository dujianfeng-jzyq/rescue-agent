"""通风图几何提取（scripts/map）：DXF 容错扫描、文字清理、圆弧近似、输出数据的基本不变量。"""

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "map"))

import dxfscan  # noqa: E402
import extract_geometry as X  # noqa: E402
import geometry  # noqa: E402
import load_drawing  # noqa: E402


def test_clean_text_strips_format_codes():
    assert dxfscan.clean_text("1339m\\S3^;/min") == "1339m³/min"
    assert dxfscan.clean_text("{\\fSimSun|b0;211016采面}") == "211016采面"
    assert dxfscan.clean_text("\\pt59.5;1370轨道石门") == "1370轨道石门"
    assert dxfscan.clean_text("a\\Pb") == "a b"


def test_read_pairs_joins_continuation_lines(tmp_path):
    # LibreDWG 输出的文字值里有未转义的换行：非整数的行并入上一个值
    p = tmp_path / "t.dxf"
    p.write_text("  0\nMTEXT\n  1\n第一行\n第二行\n 10\n1.5\n", encoding="utf-8")
    assert dxfscan.read_pairs(p) == [(0, "MTEXT"), (1, "第一行\n第二行"), (10, "1.5")]


def test_bulge_semicircle_ccw_passes_right_of_chord():
    pts = geometry._bulge_pts((0, 0), (2, 0), 1.0)
    assert pts[-1] == (2, 0) or math.isclose(pts[-1][0], 2) and math.isclose(pts[-1][1], 0, abs_tol=1e-9)
    mid = min(pts, key=lambda p: abs(p[0] - 1))
    assert math.isclose(mid[1], -1.0, abs_tol=0.02)  # 逆时针半圆经过弦的右侧
    cw = geometry._bulge_pts((0, 0), (2, 0), -1.0)
    assert min(cw, key=lambda p: abs(p[0] - 1))[1] > 0.98


def _hatch(tags):
    return dxfscan.Entity("HATCH", "ENTITIES", None, tags)


def test_hatch_polyline_path_with_hole():
    sq = lambda x0, y0, x1, y1: [(72, "0"), (73, "1"), (93, "4"), (10, str(x0)), (20, str(y0)), (10, str(x1)), (20, str(y0)),  # noqa: E731
                                 (10, str(x1)), (20, str(y1)), (10, str(x0)), (20, str(y1)), (97, "0")]
    tags = [(8, "0"), (62, "252"), (70, "1"), (91, "2"), (92, "7"), *sq(0, 0, 10, 10), (92, "6"), *sq(4, 4, 6, 6), (75, "0")]
    (f,) = geometry.hatch_fills(_hatch(tags))
    assert f.aci == 252 and len(f.holes) == 1 and len(f.outer) >= 4


def test_hatch_edges_circle_and_pattern_hatch_ignored():
    # 外边界由一条整圆弧（边类型 2）组成：所有点离圆心 2
    tags = [(70, "1"), (91, "1"), (92, "1"), (93, "1"), (72, "2"), (10, "5"), (20, "5"), (40, "2"), (50, "0"), (51, "360"), (73, "1"), (97, "0")]
    (f,) = geometry.hatch_fills(_hatch(tags))
    assert {round(math.hypot(x - 5, y - 5), 3) for x, y in f.outer} == {2.0}
    # 图案填充（70=0，GRAVEL 等）不画
    assert geometry.hatch_fills(_hatch([(70, "0"), (91, "1")])) == []


def _dxf(entities):
    out = ["0", "SECTION", "2", "ENTITIES"]
    for t, tags in entities:
        out += ["0", t]
        for c, v in tags:
            out += [str(c), str(v)]
    out += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(out) + "\n"


def test_extract_end_to_end_on_synthetic_drawing(tmp_path):
    sq = [(72, "0"), (73, "1"), (93, "4"), (10, "0"), (20, "0"), (10, "4"), (20, "0"), (10, "4"), (20, "4"), (10, "0"), (20, "4"), (97, "0")]

    def hatch(dx, dy):
        shifted = [(c, str(float(v) + (dx if c == 10 else dy if c == 20 else 0))) if c in (10, 20) else (c, v) for c, v in sq]
        return ("HATCH", [(8, "0"), (62, "252"), (70, "1"), (91, "1"), (92, "7"), *shifted, (75, "0")])

    ents = [
        ("LINE", [(8, "0"), (10, 1000), (20, 1000), (11, 1100), (21, 1000)]),            # 巷道
        ("LINE", [(8, "0"), (10, 1000), (20, 1000), (11, 1100), (21, 1000)]),            # 重复，应去重
        ("LINE", [(8, "0"), (10, 1000), (20, 1100), (11, 1000), (21, 1100 + 50)]),
        ("LINE", [(8, "0"), (10, 0), (20, 1500), (11, 6000), (21, 1500)]),               # 图框：太宽
        ("MTEXT", [(8, "0"), (10, 1050), (20, 1010), (40, 3.5), (1, "211016采面")]),
        ("MTEXT", [(8, "0"), (10, 1060), (20, 1020), (40, 3.5), (1, "1339m\\S3^;/min")]),
        ("LINE", [(8, "0"), (10, 2010), (20, 20), (11, 2030), (21, 20)]),               # 图例框里的符号
        ("TEXT", [(8, "0"), (10, 2040), (20, 20), (40, 2.5), (1, "防火门")]),
        ("TEXT", [(8, "0"), (10, 3010), (20, 3010), (40, 5), (1, "标题栏")]),              # 要丢掉的区域
        hatch(1010, 1010),                                                              # 巷道本体
        hatch(8000, 8000),                                                              # 杂散填充：落在折线和标注范围之外
    ]
    dxf = tmp_path / "t.dxf"
    dxf.write_text(_dxf(ents), encoding="utf-8")
    cfg = {"select": {"x": [0, 10000], "y": [0, None]}, "legend": [2000, 2100, 0, 100], "exclude": [[3000, 3100, 3000, 3100]],
           "frame_min_width": 5000, "frame_min_height": 5000, "title": "测试图"}
    out = X.extract(load_drawing.load(dxf), cfg, "t")
    assert out["title"] == "测试图" and out["stats"]["polylines"] == 2
    assert out["stats"]["dropped"] == {"frame": 1, "excluded": 1, "duplicate": 1, "stray_fill": 1}
    assert list(out["names"]) == ["211016采面"]
    assert [lb["text"] for lb in out["labels"] if lb["kind"] == "flow"] == ["1339m³/min"]
    assert out["legend"] and [lb["text"] for lb in out["legend"]["labels"]] == ["防火门"]
    assert out["legend"]["labels"][0]["x"] == 40 and out["legend"]["labels"][0]["y"] == 20  # 相对图例框左下角
    assert sum(len(v) for v in out["fills"].values()) == 1
    assert out["bbox"][0] == 0 and out["bbox"][1] == 0 and out["bbox"][2] <= 120  # 杂散填充没有撑大范围


def test_extract_config_missing_is_a_clear_error(tmp_path):
    with pytest.raises(SystemExit, match="extract.example.json"):
        X.load_config(tmp_path / "nope.json")


DATA = ROOT / "var" / "map" / "geometry.json"


@pytest.mark.skipif(not DATA.exists(), reason="真实矿井图不进仓库：var/map/geometry.json 在本机才有（scripts/map/extract_geometry.py 生成）")
def test_iso_geometry_invariants():
    d = json.loads(DATA.read_text(encoding="utf-8"))
    w, h = d["bbox"][2], d["bbox"][3]
    # 图框、图例、标题栏已剔除：范围是图纸里巷道的范围
    assert 1300 < w < 1600 and 700 < h < 900
    for layer in d["layers"].values():
        for line in layer["lines"]:
            xs, ys = line["p"][0::2], line["p"][1::2]
            assert len(xs) == len(ys) >= 2
            assert min(xs) >= -0.01 and max(xs) <= w + 0.01 and min(ys) >= -0.01 and max(ys) <= h + 0.01
    # 主线（211016 采面）的三个名称都在定位索引里，且各只有一个标注
    for name in ("211016采面", "211016回风巷", "211016运输巷"):
        assert len(d["names"][name]) == 1
    ids = {lb["id"] for lb in d["labels"]}
    assert all(i in ids for v in d["names"].values() for i in v)
    # 没有残留的格式码
    assert not any("\\" in lb["text"] for lb in d["labels"])
