"""实时红线报警：甲烷超限且上升（第75条）、封闭或启封后火区 CO 持续上升的复燃征兆（第164条，只对火区台账里的火区）。

输入：{"evidence": <证据内容>}。输出 result：alarms（rule_id、article、sensor_id、message）、count、reseal_indicated（火区已启封且有复燃报警，应重新封闭）。
有报警时应立即提示指挥人员，不等待其他研判。

另外列出矿井报警平台上本工作面测点未恢复的报警（platform_alarms、platform_count、platform_available）：那是矿井自己的越限报警，
不是这里的规程红线报警，单独列出，免得“实时报警：无”和大屏上的报警看起来矛盾（2026-11-10 演示复看）。
"""

from _alarms import check_all, reseal_indicated
from _skill import evidence, load_reference, main

VERSION = "alarms/1.0.0"


def run(data, _args):
    ev = evidence(data)
    cfg = load_reference("alarms.yaml")
    warnings: list[str] = []
    found = check_all(ev, cfg, warnings)
    params = {"methane": cfg["methane"], "reignition": cfg["reignition"], "status": cfg["status"]}
    platform = platform_alarms(ev)
    return {
        "panel": ev["panel"],
        "as_of": ev["as_of"],
        "alarms": found,
        "count": len(found),
        "reseal_indicated": reseal_indicated(ev, found),
        "platform_available": platform is not None,
        "platform_alarms": platform or [],
        "platform_count": len(platform or []),
        "platform_text": "、".join(f"{a['sensor_name']} {a['measurand']}" for a in platform or []),
    }, params, warnings


def platform_alarms(ev):
    """矿井报警平台上本工作面测点（证据里按工作面取到的测点）未恢复的报警；证据里没有采集报警平台时为 None。"""
    if ev.get("mine_alarms") is None:
        return None
    names = {s["id"]: s.get("name") or s["id"] for g in ev["groups"].values() for s in g["sensors"] if s.get("panel") == ev["panel"]}
    out = []
    for a in ev["mine_alarms"]:
        if a.get("sensor_id") in names and a.get("status", "active") == "active":
            out.append({"alarm_id": a.get("alarm_id"), "sensor_id": a["sensor_id"], "sensor_name": names[a["sensor_id"]], "measurand": a.get("measurand"),
                        "value": a.get("value"), "threshold": a.get("threshold"), "raised_at": a.get("raised_at")})  # fmt: skip
    return sorted(out, key=lambda a: (str(a["raised_at"]), a["sensor_id"]))


if __name__ == "__main__":
    main(VERSION, run)
