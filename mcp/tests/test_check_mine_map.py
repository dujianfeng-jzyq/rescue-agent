"""地图映射表的一致性检查（scripts/map/check_mine_map.py）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "map"))
import check_mine_map as C  # noqa: E402

NET = {
    "nodes": [{"id": "A", "name": "甲"}, {"id": "B", "name": "乙"}, {"id": "C", "name": "丙"}, {"id": "D", "name": "丁"}],
    "roadways": [
        {"id": "R1", "name": "r1", "from_node": "A", "to_node": "B", "length_m": 100},
        {"id": "R2", "name": "r2", "from_node": "B", "to_node": "C", "length_m": 100},
        {"id": "R3", "name": "r3", "from_node": "C", "to_node": "D", "length_m": 100},
    ],
}
GEO = {"bbox": [0, 0, 1000, 800], "names": {"采面": [], "回风巷": []}}


def good_map():
    return {"status": "reviewed", "reviewed_by": "张三", "nodes": {"A": {"at": [0, 0]}, "B": {"at": [100, 0]}, "C": {"at": [200, 0]}, "D": {"at": [300, 0]}}, "roadways": {"R1": "采面"}}


def test_good_mapping_passes_without_errors_or_notes():
    errors, warnings, notes = C.check(NET, good_map(), GEO)
    assert errors == [] and warnings == [] and notes == []


def test_missing_and_out_of_range_nodes_are_errors():
    m = good_map()
    del m["nodes"]["C"]
    m["nodes"]["D"] = {"at": [9000, 0]}
    errors, _, _ = C.check(NET, m, GEO)
    assert any("节点 C" in e and "没有位置" in e for e in errors)
    assert any("节点 D" in e and "范围" in e for e in errors)


def test_stale_mapping_entries_are_errors():
    m = good_map()
    m["nodes"]["Z"] = {"at": [1, 1]}
    m["roadways"]["R9"] = "采面"
    errors, _, _ = C.check(NET, m, GEO)
    assert any("节点 Z" in e and "网络里没有" in e for e in errors)
    assert any("巷道 R9" in e for e in errors)


def test_invalid_via_is_error_and_unknown_name_is_warning():
    m = good_map()
    m["roadways"]["R2"] = {"name": "不存在", "via": [[1, 2], "x"]}
    errors, warnings, _ = C.check(NET, m, GEO)
    assert any("R2" in e and "via" in e for e in errors)
    assert any("R2" in w and "不在图上" in w for w in warnings)


def test_isolated_node_and_length_ratio_outlier_are_warnings():
    net = {"nodes": NET["nodes"] + [{"id": "E", "name": "戊"}], "roadways": NET["roadways"]}
    m = good_map()
    m["nodes"]["E"] = {"at": [10, 10]}
    m["nodes"]["D"] = {"at": [700, 700]}  # R3 的折线比其他巷道长很多（节点摆错了）
    _, warnings, _ = C.check(net, m, GEO)
    assert any("节点 E 孤立" in w for w in warnings)
    assert any("R3" in w and "相差超过" in w for w in warnings)


def test_schematic_mapping_gets_a_note():
    m = good_map()
    m["status"] = "placeholder"
    _, _, notes = C.check(NET, m, GEO)
    assert notes and "示意映射" in notes[0]
