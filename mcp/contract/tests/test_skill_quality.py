"""技能的数据质量检测（fire-assessment/scripts/_quality.py）和定级时的保守处理（_warning.py）。

2026-11-11 代码复查第 4 轮：真实的读数突升不能被当成孤立尖峰剔除；读数停在预警阈值以上（满量程）不能整段排除让等级掉下来。
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "skills" / "fire-assessment" / "scripts"


@pytest.fixture(scope="module")
def mods():
    sys.path.insert(0, str(SCRIPTS))  # 脚本 import 同目录的辅助模块
    try:
        import _quality
        import _warning

        yield _quality, _warning
    finally:
        sys.path.remove(str(SCRIPTS))
        for name in ("_quality", "_warning", "_evidence"):
            sys.modules.pop(name, None)  # 别让别的技能的同名辅助模块读到这份


def readings(vals, sid="S", m="CO"):
    return [{"sensor_id": sid, "measurand": m, "ts": f"2026-11-05T{h:02d}:00:00", "value": v, "quality": "ok"} for h, v in enumerate(vals)]


BASE = [10, 11, 10, 12, 11, 10, 11, 12]


@pytest.mark.parametrize("k", [1, 2, 3, 4])
def test_real_step_rise_is_kept(mods, k):
    q, _ = mods
    r = q.check_series("S/CO", readings(BASE + [200] * k))
    assert r.usable[-1]["value"] == 200 and len(r.usable) == len(BASE) + k  # 一个都不剔除
    assert not any("孤立尖峰" in f for f in r.flags)


def test_newest_jump_is_flagged_for_confirmation(mods):
    q, _ = mods
    r = q.check_series("S/CO", readings(BASE + [200]))
    assert r.flags == ["S/CO：最新读数突升（200），后面还没有读数确认，按保守处理保留"]


def test_isolated_spike_is_still_removed(mods):
    q, _ = mods
    r = q.check_series("S/CO", readings(BASE + [200, 11, 10]))
    assert 200 not in [x["value"] for x in r.usable] and r.flags == ["S/CO：1 个孤立尖峰，疑似干扰，已剔除"]


def test_stuck_at_or_above_threshold_is_kept(mods):
    q, _ = mods
    r = q.check_series("S/CO", readings([300, 500, 800] + [1000] * 6), keep_at_or_above=40)
    assert r.usable[-1]["value"] == 1000 and "按保守处理保留" in r.flags[0]
    # 不给阈值（监听的必需测点检查）照旧按卡值排除
    assert q.check_series("S/CO", readings([300, 500, 800] + [1000] * 6)).usable == []
    # 低于阈值的卡值照旧排除（传感器故障不误报）
    low = q.check_series("S/CO", readings([3] * 8), keep_at_or_above=40)
    assert low.usable == [] and "已排除" in low.flags[0]


def evidence(tube_co, uc_co=None):
    """一个工作面：束管 BT-1201-G1（CO、O2、N2），可选上隅角 SM-1201-UC。"""
    sensors = [{"id": "BT-1201-G1", "source": "bundle_tube", "measurands": ["CO", "O2", "N2"]}]
    rs = readings(tube_co, "BT-1201-G1", "CO") + readings([12.0] * len(tube_co), "BT-1201-G1", "O2") + readings([75.0 + i * 0.1 for i in range(len(tube_co))], "BT-1201-G1", "N2")
    if uc_co is not None:
        sensors.append({"id": "SM-1201-UC", "source": "safety_monitor", "measurands": ["CO"]})
        rs += readings(uc_co, "SM-1201-UC", "CO")
    measurands = {s["id"]: s["measurands"] for s in sensors}
    group = {"sensors": sensors, "measurands": measurands, "latest": [], "readings": rs, "window_h": 48}
    as_of = rs[len(tube_co) - 1]["ts"]
    return {"panel": "1201", "as_of": as_of, "groups": {"g": group}, "missing": []}


def test_saturated_tube_keeps_red_level(mods):
    _, w = mods
    th = {"blue": {"tube_co_ppm": 40}, "yellow": {"tube_co_ppm": 80}, "orange": {"tube_co_ppm": 200}, "red": {"tube_co_ppm": 1000}}
    out = w.assess(evidence([300, 500, 800] + [1000] * 6), th, 24, 6)
    assert out["level"] == "red" and any("满量程" in x for x in out["data_quality"])


def test_missing_upper_corner_is_stated(mods):
    _, w = mods
    th = {"blue": {"tube_co_ppm": 40}}
    out = w.assess(evidence([5, 6, 5, 6]), th, 24, 6)
    assert "证据里没有 SM-1201-UC/CO 的读数，上隅角 CO 不参与分级" in out["data_quality"]
    with_uc = w.assess(evidence([5, 6, 5, 6], uc_co=[3, 4, 3, 4]), th, 24, 6)
    assert not any("SM-1201-UC/CO 的读数" in x for x in with_uc["data_quality"])
