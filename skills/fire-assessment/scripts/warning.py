"""预警等级：按标志气体判定工作面采空区自燃预警等级（none/blue/yellow/orange/red）。

输入：{"evidence": <证据内容>}。输出 result：等级、触发条件、指标值（indicators 保留 3 位小数，values 为原值）、
每个指标的来源测点、数据质量说明、注意事项。可疑数据已排除。
"""

from _skill import evidence, load_reference, main
from _warning import assess

VERSION = "warning/1.0.0"


def run(data, _args):
    th = load_reference("thresholds.yaml")
    result = assess(evidence(data), th["levels"], th["window_h"], th["rate_window_h"])
    params = {"thresholds": th["levels"], "window_h": th["window_h"], "rate_window_h": th["rate_window_h"], "status": th["status"]}
    return result, params, ["阈值为开发占位值，待专家按本矿自然发火预测预报制度审定"] if th["status"] == "placeholder" else []


if __name__ == "__main__":
    main(VERSION, run)
