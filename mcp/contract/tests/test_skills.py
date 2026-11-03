"""技能运行器与自检（rescue_contract.skills）：用临时技能目录覆盖各种失败情况。"""

import json
import textwrap
from pathlib import Path

import pytest
from rescue_contract.skills import SkillError, check, discover, load_skill, run_script

SCRIPT = textwrap.dedent(
    '''
    import argparse, json, os, sys, time
    from pathlib import Path
    import yaml
    from _lib import double

    ap = argparse.ArgumentParser()
    ap.add_argument("--input-file"); ap.add_argument("--output-file"); ap.add_argument("--svg-out")
    a = ap.parse_args()
    inp = json.loads(Path(a.input_file).read_text(encoding="utf-8"))
    params = yaml.safe_load((Path(__file__).parent.parent / "references" / "params.yaml").read_text(encoding="utf-8"))
    mode = inp.get("mode", "ok")
    if mode == "sleep":
        time.sleep(30)
    if mode == "fail":
        print("缺少测点 BT-1201-G1", file=sys.stderr); sys.exit(3)
    if mode == "badjson":
        Path(a.output_file).write_text("不是 JSON", encoding="utf-8"); sys.exit(0)
    if mode == "nokeys":
        Path(a.output_file).write_text(json.dumps({"result": 1}), encoding="utf-8"); sys.exit(0)
    out = {"algorithm_version": "t-1", "params": {"k": params["k"]}, "warnings": [],
           "result": {"value": double(inp["x"]) * params["k"], "env": sorted(k for k in os.environ if "KEY" in k or "TOKEN" in k),
                      "cwd_is_skill": Path.cwd().resolve() == Path(__file__).parent.resolve()}}
    if a.svg_out and mode != "nosvg":
        Path(a.svg_out).write_text("<svg/>", encoding="utf-8")
    Path(a.output_file).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    '''
)


def make_skill(root: Path, name="demo-skill", *, script=SCRIPT, frontmatter=None, manifest=None, params="status: placeholder\nreviewed_by: null\nk: 1.5\n", cases=None) -> Path:
    d = root / name
    (d / "scripts").mkdir(parents=True)
    (d / "references").mkdir()
    fm = frontmatter if frontmatter is not None else f"name: {name}\ndescription: 测试技能\nwhenToUse: 测试时\nversion: 0.1.0\n"
    (d / "SKILL.md").write_text(f"---\n{fm}---\n\n# 测试\n", encoding="utf-8")
    (d / "skill.yaml").write_text(manifest or "scripts:\n  calc:\n    file: scripts/calc.py\n    description: 测试\n    timeout_s: 2\n    svg: true\n", encoding="utf-8")
    (d / "scripts" / "calc.py").write_text(script, encoding="utf-8")
    (d / "scripts" / "_lib.py").write_text("def double(x):\n    return 2 * x\n", encoding="utf-8")
    (d / "references" / "params.yaml").write_text(params, encoding="utf-8")
    for case, (inp, exp) in (cases if cases is not None else {"basic": ({"x": 2}, None)}).items():
        cd = d / "tests" / "cases" / "calc"
        cd.mkdir(parents=True, exist_ok=True)
        (cd / f"{case}.input.json").write_text(json.dumps(inp), encoding="utf-8")
        if exp is None:
            exp = {"algorithm_version": "t-1", "params": {"k": 1.5}, "warnings": [], "result": {"value": inp["x"] * 3.0, "env": [], "cwd_is_skill": False}}
        if exp != "missing":
            (cd / f"{case}.expected.json").write_text(json.dumps(exp), encoding="utf-8")
    (root / "requirements.txt").write_text("pyyaml>=6\n", encoding="utf-8")
    return d


def test_check_passes(tmp_path):
    make_skill(tmp_path)
    (tmp_path / "assessment").mkdir()  # 第一版的角色目录（没有 skill.yaml）不算
    r = check(tmp_path)
    assert r.ok, (r.problems, r.cases)
    assert r.skills == ["demo-skill"] and len(r.cases) == 1


def test_child_gets_no_credentials_and_no_bytecode(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    monkeypatch.setenv("RESCUE_MCP_TOKEN", "t")
    d = make_skill(tmp_path)
    r = run_script(load_skill(d).scripts["calc"], {"x": 2})
    assert r.ok and r.output["result"]["env"] == []
    assert r.svg == "<svg/>"
    assert not list(d.rglob("__pycache__"))


@pytest.mark.parametrize(
    ("mode", "status"),
    [("sleep", "timeout"), ("fail", "exit"), ("badjson", "invalid-output"), ("nokeys", "schema"), ("nosvg", "schema")],
)
def test_run_classifies_failures(tmp_path, mode, status):
    spec = load_skill(make_skill(tmp_path)).scripts["calc"]
    r = run_script(spec, {"x": 2, "mode": mode}, timeout_s=1 if mode == "sleep" else None)
    assert r.status == status, r.detail
    if mode == "fail":
        assert "BT-1201-G1" in r.detail


def test_spawn_error(tmp_path):
    spec = load_skill(make_skill(tmp_path)).scripts["calc"]
    assert run_script(spec, {"x": 1}, python=str(tmp_path / "no-python")).status == "spawn-error"


def test_expected_mismatch_reports_path(tmp_path):
    make_skill(tmp_path, cases={"wrong": ({"x": 2}, {"algorithm_version": "t-1", "params": {"k": 1.5}, "warnings": [], "result": {"value": 7, "env": [], "cwd_is_skill": False}})})
    r = check(tmp_path)
    assert not r.ok and "$.result.value" in r.cases[0].detail


def test_missing_cases_and_expected(tmp_path):
    make_skill(tmp_path, cases={"a": ({"x": 1}, "missing")})
    make_skill(tmp_path, name="other-skill", cases={})
    r = check(tmp_path)
    assert any("缺少 a.expected.json" in c.detail for c in r.cases)
    assert any("other-skill：脚本 calc 没有自检样例" in p for p in r.problems)


def test_disallowed_import(tmp_path):
    make_skill(tmp_path, script="import requests\n" + SCRIPT)
    assert any("import requests" in p for p in check(tmp_path).problems)


@pytest.mark.parametrize(
    ("params", "msg"),
    [("k: 1\n", "status"), ("status: reviewed\nk: 1\n", "reviewed_by"), ("status: final\nk: 1\n", "status")],
)
def test_reference_status(tmp_path, params, msg):
    make_skill(tmp_path, params=params, cases={})
    assert any(msg in p for p in check(tmp_path).problems)


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        ({"frontmatter": "name: demo-skill\ndescription: x\n"}, "缺少 whenToUse, version"),
        ({"frontmatter": "name: other\ndescription: x\nwhenToUse: y\nversion: 0.1.0\n"}, "目录名必须"),
        ({"frontmatter": "name: demo-skill\ndescription: x\nwhenToUse: y\nversion: 1\n"}, "x.y.z"),
        ({"manifest": "scripts:\n  calc:\n    file: ../../etc/passwd\n"}, "scripts/"),
        ({"manifest": "scripts:\n  calc:\n    file: scripts/missing.py\n"}, "不存在"),
        ({"manifest": "scripts:\n  Calc:\n    file: scripts/calc.py\n"}, "脚本名"),
    ],
)
def test_load_skill_errors(tmp_path, kw, msg):
    d = make_skill(tmp_path, **kw)
    with pytest.raises(SkillError, match=msg):
        load_skill(d)
    assert any(msg in p for p in check(tmp_path).problems)


def test_discover_only_v2(tmp_path):
    make_skill(tmp_path)
    (tmp_path / "shared" / "report-templates").mkdir(parents=True)
    assert [s.name for s in discover(tmp_path)] == ["demo-skill"]
