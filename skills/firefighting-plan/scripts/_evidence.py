"""读证据内容（采集清单取到的数据，skills/README.md“采集清单”）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from _quality import Reading


def as_of(ev: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(ev["as_of"])


def sensors(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """证据里全部测点（各测点组合并，按 id 去重排序）。"""
    out: dict[str, dict[str, Any]] = {}
    for g in ev["groups"].values():
        for s in g["sensors"]:
            out.setdefault(s["id"], s)
    return [out[k] for k in sorted(out)]


def series(ev: dict[str, Any], sensor_id: str, measurand: str, window_h: float) -> list[Reading]:
    """某测点某参数在 [as_of − window_h, as_of] 内的读数，按时间排序。

    读数取自含该测点、该参数的各测点组。证据里没有这个测点或参数时为空（与矿井没有这个测点一样，按缺数处理）；
    有，但这些组的窗口都短于 window_h 时抛 ValueError（采集清单与脚本不配套，不能算）。
    """
    groups = [g for g in ev["groups"].values() if measurand in g["measurands"].get(sensor_id, [])]
    if not groups:
        return []
    if max(g["window_h"] for g in groups) < window_h:
        raise ValueError(f"证据里 {sensor_id}/{measurand} 的时间窗口不足 {window_h} 小时，检查采集清单")
    start = as_of(ev) - timedelta(hours=window_h)
    end = as_of(ev)
    seen: dict[str, Reading] = {}
    for g in groups:
        for r in g["readings"]:
            if r["sensor_id"] == sensor_id and r["measurand"] == measurand and start <= datetime.fromisoformat(r["ts"]) <= end:
                seen.setdefault(r["ts"], r)
    return [seen[k] for k in sorted(seen, key=datetime.fromisoformat)]


def has(ev: dict[str, Any], sensor_id: str, measurand: str) -> bool:
    return any(measurand in g["measurands"].get(sensor_id, []) for g in ev["groups"].values())
