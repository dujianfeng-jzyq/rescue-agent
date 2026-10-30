"""智能体端到端评测的判定逻辑（eval/agent/grade.py），用合成的 DSH 会话日志测试。"""

import json
import sys
from pathlib import Path

import pytest
from compression import zstd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval" / "agent"))
import grade  # noqa: E402

ROOT_ID = "session-root"
SUB_ID = "sub-1"


def _assistant(text: str, calls: list[tuple[str, str, dict]] = ()) -> list[dict]:
    content = [{"type": "text", "text": text}] + [{"type": "tool-call", "id": cid, "name": n, "arguments": json.dumps(a)} for cid, n, a in calls]
    out = [{"type": "assistant/message", "data": {"message": {"role": "assistant", "content": content, "source": {"provider": "p", "model": "m"}}, "usage": {"totalTokens": 10}}}]
    for cid, n, a in calls:
        out.append({"type": "tool/call", "data": {"callId": cid, "name": n, "arguments": json.dumps(a, ensure_ascii=False)}})
    return out


def _result(cid: str, text: str) -> dict:
    return {"type": "tool/result", "data": {"message": {"toolCallId": cid, "content": [{"type": "text", "text": text}]}}}


def _user(text: str, kind: str = "user") -> dict:
    return {"type": "user/message", "data": {"content": [{"type": "text", "text": text}], "source": {"kind": kind}}}


def _write(base: Path, sid: str, events: list[dict], preset: str, parent: str | None = None) -> None:
    head = {"type": "session", "id": sid, "createdAt": 1, "agentPreset": preset, **({"parentSession": parent} if parent else {})}
    d = base / "ws" / sid
    d.mkdir(parents=True)
    raw = "\n".join(json.dumps(e, ensure_ascii=False) for e in [head, *events]) + "\n"
    (d / "session.v4.jsonl.zstd").write_bytes(zstd.compress(raw.encode()))


@pytest.fixture
def tree(tmp_path):
    def build(sub_report: str, sub_calls=(("c2", "mcp__fire_model__assess_warning_level", {"panel": "1201"}),), root_calls=()):
        turn = [{"type": "turn/start"}]
        end = [{"type": "turn/end"}]
        root = [
            *turn,
            _user("请研判 1201 工作面"),
            _user("Current runtime context 3080", kind="runtime-context"),
            *_assistant("委派研判", [("c1", "delegate", {"agent_preset": "rescue-assessment", "prompt": "研判 1201"}), *root_calls]),
            _result("c1", "started subagent sub-1"),
            *end,
        ]
        sub = [*turn, _user("研判 1201"), *_assistant("", list(sub_calls))]
        sub += [_result(cid, '{"level": "none", "indicators": {"上隅角CO(ppm)": 9.353, "格雷哈姆系数": 0.021}, "depth_m": 20.0}') for cid, _, _ in sub_calls]
        sub += [*_assistant(sub_report), *end]
        _write(tmp_path, ROOT_ID, root, "rescue-commander")
        _write(tmp_path, SUB_ID, sub, "rescue-assessment", ROOT_ID)
        return grade.load_tree(ROOT_ID, tmp_path)

    return build


ALL = {"delegates_to": "rescue-assessment", "no_actions": True, "numbers_grounded": True, "cited_tools_called": True}


def test_numbers_strip_ids_dates_and_article_numbers():
    text = "1. 2026-11-04T15:03:02 BT-1201-G1 使G1、C2H4 第85条 第 1、3、5 节 11-03 06:00 11-04T08:00 #16\n## 2. 上隅角CO 9.35 ppm"
    assert [t for t, _ in grade.numbers(text)] == ["9.35"]


@pytest.mark.parametrize(
    ("token", "level"),
    [("9.353", "exact"), ("9.35", "exact"), ("9.4", "exact"), ("20", "exact"), ("9", "approx"), ("9.3", "none"), ("8", "none"), ("1.0", "none")],
)
def test_match_level(token, level):
    assert grade.match_level(token, {"9.353", "20.0", "0.021"}) == level


def test_grounded_report_passes(tree):
    r = grade.grade(tree("预警等级 none，上隅角CO 9.35 ppm（fire_model 计算结果），格雷哈姆系数 0.021，20 m 深处"), ALL)
    assert r["passed"], r["checks"]
    assert r["settled"]
    assert [s["preset"] for s in r["sessions"]] == ["rescue-commander", "rescue-assessment"]


def test_invented_threshold_fails(tree):
    r = grade.grade(tree("若 CO 上升速率超过 1.0 ppm/h，建议束管加密至 30 分钟/次"), ALL)
    bad = r["checks"]["numbers_grounded"]["detail"]["rescue-assessment:sub-1"]
    assert [b["value"] for b in bad] == ["1.0", "30"]
    assert not r["passed"]


def test_runtime_context_is_not_material(tree):
    # 3080 只出现在系统注入的运行时上下文里，不算 Agent 看到的材料
    r = grade.grade(tree("端口 3080"), ALL)
    assert not r["checks"]["numbers_grounded"]["ok"]


def test_cited_server_not_called_fails(tree):
    r = grade.grade(tree("依据：ventnet 通风网络分析"), ALL)
    assert r["checks"]["cited_tools_called"]["detail"] == {"rescue-assessment:sub-1": ["ventnet"]}


def test_preset_name_is_not_a_server_citation(tree):
    r = grade.grade(tree("用量交 rescue-dispatch 估算"), ALL)
    assert r["checks"]["cited_tools_called"]["ok"]


def test_citation_backed_by_parent_call_passes(tree):
    r = grade.grade(tree("依据：ventnet 通风网络分析", root_calls=[("c3", "mcp__ventnet__escape_route", {})]), ALL)
    assert r["checks"]["cited_tools_called"]["ok"]


def test_actions_call_fails_no_actions(tree):
    r = grade.grade(tree("已建议发布", sub_calls=[("c2", "mcp__actions__publish_warning", {})]), ALL)
    assert r["checks"]["no_actions"] == {"ok": False, "detail": ["mcp__actions__publish_warning"]}


def test_delegation_to_wrong_preset_fails(tree):
    r = grade.grade(tree("无"), {"delegates_to": "rescue-plan"})
    assert r["checks"]["delegates_to"] == {"ok": False, "detail": ["rescue-assessment"]}
