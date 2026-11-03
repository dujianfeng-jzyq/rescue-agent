"""自燃发展阶段：按预警判定的指标原值（warning 的 values）判定阶段，与预警等级独立。

输入：{"warning": <warning.py 的输出>}。输出 result：stage（unknown/no_sign/slow_oxidation/accelerating/intense）、
stage_name、stage_basis。不要据阶段推算煤温。
"""

from _skill import load_reference, main, record_result
from _warning import STAGE_NAMES, classify_stage

VERSION = "stage/1.0.0"


def run(data, _args):
    w = record_result(data, "warning")
    th = load_reference("thresholds.yaml")
    stage, basis = classify_stage(w["values"], th["stage"])
    result = {"panel": w["panel"], "as_of": w["as_of"], "stage": stage, "stage_name": STAGE_NAMES[stage], "stage_basis": basis}
    warnings = ["阶段判据为开发占位值，待专家按本矿煤样程序升温实验审定"] if th["status"] == "placeholder" else []
    return result, {"stage": th["stage"], "status": th["status"]}, warnings


if __name__ == "__main__":
    main(VERSION, run)
