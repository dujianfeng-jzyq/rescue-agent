"""决策检查：用例写好“期望的决策 B”，从会话日志提取“实际的决策 B1”，比较（智能体评测的 D 类检查）。

只描述决策，不描述文字：智能体提交了哪些对外动作、参数是什么、调了哪些流程、做了哪些事件操作。

用例里的写法（checks.expect_decisions）：

    expect_decisions:
      status: placeholder                      # 占位，待专家审定
      derived_from: {...}                      # 生成脚本（gen_expected.py）写入的依据，只给人看
      external_actions:                        # 对外动作（actions 服务的工具）；封闭集合：required 和 optional 之外的算多做
        required:
          escalate_emergency: {}                 # 至少提交一次（升级的理由和级别由 guard 按记录生成，参数不约束）
          publish_warning: {level: red}          # 每次提交的参数都要包含这些键值
        optional: {}                           # 可以提交也可以不提交（参数同样要匹配）
      flows_called: [run_fire_assessment]      # 这些工具（短名）至少调用过一次
      flows_forbidden: [run_fire_assessment]   # 这些工具一次都不该调用（如重复投递不该重新研判）
      questions: {min: 1}                      # 向人提问（ask_user、ask_user_question）的次数范围；缺信息时必须问，其余情形 max: 0
      incident_ops:                            # 事件相关工具（get_incident、add_followup 等）
        required: [complete_followup]
        forbidden: [close_incident]

对外动作按“提交”算：调用了就算，不管人工确认是放行还是拒绝。参数比较前先把工具参数里唯一的那层对象展开
（{"notice": {"level": "red", ...}} 当作 {"level": "red", ...}）。
没有写的部分不检查；external_actions 一旦写了就是封闭的。
"""

from __future__ import annotations

import json

ACTIONS_PREFIX = "mcp__actions__"
# 只读、不是对外动作的 actions 工具（动作分级表里的 A 级）：不计入“多做”
READ_ONLY_ACTIONS = {"precheck_plan", "query_last_issued"}


CODE_CARD_HEADERS = {"处置选项", "现场条件"}


def is_code_card(arguments: str | None) -> bool:
    """ask_user_question 的调用是不是代码给出的卡片（所有问题的 header 都是处置选项或现场条件）。"""
    try:
        qs = json.loads(arguments or "{}").get("questions", [])
    except ValueError:
        return False
    return bool(qs) and all(q.get("header") in CODE_CARD_HEADERS for q in qs)


def _flatten(args: dict) -> dict:
    if len(args) == 1 and isinstance(next(iter(args.values())), dict):
        return dict(next(iter(args.values())))
    return dict(args)


def extract(calls: list[tuple[str, str]]) -> dict:
    """B1：{"actions": {工具: [参数, ...]}, "tools": 全部调用过的短名}。calls 是 Session.calls()：(工具名, 参数原文)。"""
    actions: dict[str, list[dict]] = {}
    tools: set[str] = set()
    for name, raw in calls:
        short = name.split("__", 2)[2] if name.startswith("mcp__") else name
        tools.add(short)
        if name.startswith(ACTIONS_PREFIX) and short not in READ_ONLY_ACTIONS:
            try:
                args = json.loads(raw) if raw else {}
            except ValueError:
                args = {}
            actions.setdefault(short, []).append(_flatten(args if isinstance(args, dict) else {}))
    # 代码给出的卡片（处置选项、现场条件）是设计内的“选处置”和“缺信息”，不算向人提问
    questions = sum(1 for name, args in calls if name in ("ask_user", "ask_user_question") and not (name == "ask_user_question" and is_code_card(args)))
    return {"actions": actions, "tools": tools, "questions": questions}


def _params_ok(want: dict, got: dict) -> list[str]:
    return [f"{k}={got.get(k)!r}（期望 {v!r}）" for k, v in (want or {}).items() if got.get(k) != v]


def compare(expect: dict, calls: list[tuple[str, str]]) -> dict:
    """比较 B 与 B1，返回 {"ok", "problems": [...], "actual": {...}}。actual 是实际提交的对外动作摘要，写进报告供人看。"""
    b1 = extract(calls)
    problems: list[str] = []

    ext = expect.get("external_actions")
    if ext is not None:
        required, optional = ext.get("required") or {}, ext.get("optional") or {}
        for tool in required:
            if tool not in b1["actions"]:
                problems.append(f"漏做：没有提交 {tool}")
        for tool, runs in b1["actions"].items():
            if tool not in required and tool not in optional:
                problems.append(f"多做：提交了不在期望里的对外动作 {tool}")
                continue
            want = required.get(tool, optional.get(tool)) or {}
            for i, got in enumerate(runs):
                bad = _params_ok(want, got)
                if bad:
                    problems.append(f"参数不符：{tool} 第 {i + 1} 次提交 " + "；".join(bad))

    for tool in expect.get("flows_called") or []:
        if tool not in b1["tools"]:
            problems.append(f"没有调用流程 {tool}")

    for tool in expect.get("flows_forbidden") or []:
        if tool in b1["tools"]:
            problems.append(f"不该做：调用了 {tool}")

    q = expect.get("questions")
    if q is not None:
        n = b1["questions"]
        if "min" in q and n < q["min"]:
            problems.append(f"漏做：向人提问 {n} 次，至少应 {q['min']} 次")
        if "max" in q and n > q["max"]:
            problems.append(f"多问：向人提问 {n} 次，最多 {q['max']} 次")

    ops = expect.get("incident_ops") or {}
    for tool in ops.get("required") or []:
        if tool not in b1["tools"]:
            problems.append(f"漏做：没有调用事件操作 {tool}")
    for tool in ops.get("forbidden") or []:
        if tool in b1["tools"]:
            problems.append(f"不该做：调用了 {tool}")

    actual = {tool: [{k: v for k, v in a.items() if k == "level"} for a in runs] for tool, runs in b1["actions"].items()}
    return {"ok": not problems, "problems": problems, "actual": actual}
