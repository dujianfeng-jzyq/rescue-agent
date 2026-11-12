"""预警等级：按标志气体判定工作面采空区自燃预警等级（none/blue/yellow/orange/red）。

输入：{"evidence": <证据内容>, "previous": <本事件上一次的预警等级记录（本脚本的输出），可省略>}。
输出 result：等级、触发条件、指标值（indicators 保留 3 位小数，values 为原值）、
每个指标的来源测点、数据质量说明、注意事项。可疑数据已排除。
定级回差（thresholds.yaml 的 hysteresis）：给了 previous 且按阈值算出的等级更低时，上一次那一级的指标要回落到
“阈值 ×（1 − band）”以下才降级，否则保持（level_raw 是按阈值直接算出的等级，hysteresis 写明依据）。
"""

from datetime import datetime

from _skill import evidence, load_reference, main, record_result
from _warning import apply_hold, assess

VERSION = "warning/1.0.0"


def run(data, _args):
    th = load_reference("thresholds.yaml")
    ev = evidence(data)
    prev = None
    if data.get("previous") is not None:
        prev = record_result(data, "previous")
        if prev.get("panel") != ev["panel"]:
            raise ValueError(f"上一次的预警等级记录是工作面 {prev.get('panel')} 的，与证据的 {ev['panel']} 不符")
        if datetime.fromisoformat(str(prev["as_of"])) > datetime.fromisoformat(str(ev["as_of"])):
            raise ValueError(f"上一次的预警等级记录（{prev['as_of']}）晚于证据（{ev['as_of']}）")
    band = float(th["hysteresis"]["band"])
    w = assess(ev, th["levels"], th["window_h"], th["rate_window_h"])
    result = apply_hold(w, th["levels"], prev.get("level") if prev else None, prev.get("as_of") if prev else None, band)
    params = {"thresholds": th["levels"], "hysteresis": th["hysteresis"], "window_h": th["window_h"], "rate_window_h": th["rate_window_h"], "status": th["status"]}
    return result, params, ["阈值为开发占位值，待专家按本矿自然发火预测预报制度审定"] if th["status"] == "placeholder" else []


if __name__ == "__main__":
    main(VERSION, run)
