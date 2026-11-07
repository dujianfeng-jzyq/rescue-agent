"""实时红线报警：甲烷超限且上升（第75条）、封闭或启封后火区 CO 持续上升的复燃征兆（第164条，只对火区台账里的火区）。

输入：{"evidence": <证据内容>}。输出 result：alarms（rule_id、article、sensor_id、message）、count、reseal_indicated（火区已启封且有复燃报警，应重新封闭）。
有报警时应立即提示指挥人员，不等待其他研判。
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
    return {"panel": ev["panel"], "as_of": ev["as_of"], "alarms": found, "count": len(found), "reseal_indicated": reseal_indicated(ev, found)}, params, warnings


if __name__ == "__main__":
    main(VERSION, run)
