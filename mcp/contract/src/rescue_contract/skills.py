"""第二版技能：读取技能目录、按统一规则运行技能脚本、自检。规范见 skills/README.md。

运行规则与证据插件的 runScript（dsh-bundle/src/evidence/）相同：
`python -E -s -B -X utf8 <脚本> --input-file … --output-file … [--svg-out …]`，环境变量按白名单传，
工作目录是本次运行的临时目录，超时先 SIGTERM、1 秒后 SIGKILL；
结果按 ok / timeout / exit / invalid-output / schema / spawn-error 分类。

命令行：
    python -m rescue_contract.skills check [技能根目录]          # 自检：结构、import、全部样例
    python -m rescue_contract.skills run <技能> <脚本> --input-file 输入.json [--output-file 结果.json]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

SKILLS_DIR_ENV = "RESCUE_SKILLS_DIR"
DEFAULT_TIMEOUT_S = 30.0
ENV_ALLOW = ("PATH", "LANG", "LC_ALL", "TZ")
# -E 忽略 PYTHON* 环境变量，-s 不加用户 site-packages，-B 不写 __pycache__（技能目录只读），-X utf8 统一编码。
# 不用 -I：它不把脚本所在目录加进 sys.path，技能内的辅助模块就 import 不了。
PYTHON_FLAGS = ("-E", "-s", "-B", "-X", "utf8")
REQUIRED_OUTPUT_KEYS = ("algorithm_version", "params", "warnings", "result")
FRONTMATTER_KEYS = ("name", "description", "whenToUse", "version")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SCRIPT_NAME = re.compile(r"^[a-z0-9_]+$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
# requirements.txt 里的发行包名 → import 名
_IMPORT_NAMES = {"pyyaml": "yaml"}

RunStatus = Literal["ok", "timeout", "exit", "invalid-output", "schema", "spawn-error"]


def default_root() -> Path:
    env = os.environ.get(SKILLS_DIR_ENV)
    if env:
        return Path(env)
    return Path(os.environ.get("RESCUE_HOME") or Path(__file__).resolve().parents[4]) / "skills"


# ── 读取技能 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScriptSpec:
    name: str
    file: Path
    description: str
    timeout_s: float = DEFAULT_TIMEOUT_S
    svg: bool = False


@dataclass(frozen=True)
class Skill:
    name: str
    dir: Path
    version: str
    frontmatter: dict[str, Any]
    scripts: dict[str, ScriptSpec]


class SkillError(ValueError):
    pass


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        raise SkillError(f"{path}：缺少 YAML frontmatter")
    data = yaml.safe_load(m.group(1)) or {}
    if not isinstance(data, dict):
        raise SkillError(f"{path}：frontmatter 不是映射")
    return data


def load_skill(skill_dir: Path) -> Skill:
    skill_dir = Path(skill_dir)
    fm = _frontmatter(skill_dir / "SKILL.md")
    missing = [k for k in FRONTMATTER_KEYS if not fm.get(k)]
    if missing:
        raise SkillError(f"{skill_dir.name}/SKILL.md：frontmatter 缺少 {', '.join(missing)}")
    if fm["name"] != skill_dir.name or not _NAME.match(skill_dir.name):
        raise SkillError(f"{skill_dir.name}：目录名必须与 frontmatter 的 name（{fm['name']}）相同，且只用小写字母、数字和连字符")
    if not _SEMVER.match(str(fm["version"])):
        raise SkillError(f"{skill_dir.name}：version 应为 x.y.z，现在是 {fm['version']}")
    manifest = yaml.safe_load((skill_dir / "skill.yaml").read_text(encoding="utf-8")) or {}
    scripts: dict[str, ScriptSpec] = {}
    for name, spec in (manifest.get("scripts") or {}).items():
        if not _SCRIPT_NAME.match(name):
            raise SkillError(f"{skill_dir.name}：脚本名 {name} 只能用小写字母、数字和下划线")
        rel = Path(spec["file"])
        path = (skill_dir / rel).resolve()
        if rel.is_absolute() or rel.parts[0] != "scripts" or not path.is_relative_to(skill_dir.resolve() / "scripts"):
            raise SkillError(f"{skill_dir.name}：脚本 {name} 必须在本技能的 scripts/ 下")
        if not path.is_file():
            raise SkillError(f"{skill_dir.name}：脚本 {name} 的文件 {rel} 不存在")
        scripts[name] = ScriptSpec(name, path, str(spec.get("description", "")), float(spec.get("timeout_s", DEFAULT_TIMEOUT_S)), bool(spec.get("svg", False)))
    return Skill(skill_dir.name, skill_dir, str(fm["version"]), fm, scripts)


def discover(root: Path | None = None) -> list[Skill]:
    """第二版技能：根目录下有 skill.yaml 的子目录（第一版的 skills/<角色>/ 不算）。"""
    root = Path(root or default_root())
    return [load_skill(d) for d in sorted(root.iterdir()) if (d / "skill.yaml").is_file()]


# ── 运行脚本 ────────────────────────────────────────────────────────────────


@dataclass
class RunResult:
    status: RunStatus
    detail: str = ""
    output: dict[str, Any] | None = None
    duration_ms: int = 0
    stderr: str = ""
    svg: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def child_env() -> dict[str, str]:
    # 不设 PYTHON* 变量：-E 会忽略它们；不写字节码、UTF-8 用命令行参数 -B、-X utf8
    return {k: os.environ[k] for k in ENV_ALLOW if k in os.environ}


def run_script(spec: ScriptSpec, input_data: Any, *, python: str | None = None, timeout_s: float | None = None, workdir: Path | None = None) -> RunResult:
    """运行一个登记过的脚本。input_data 写成 input.json；输出读 output.json。"""
    timeout = timeout_s if timeout_s is not None else spec.timeout_s
    with tempfile.TemporaryDirectory(prefix="skill-run-") as tmp:
        run_dir = Path(workdir or tmp)
        run_dir.mkdir(parents=True, exist_ok=True)
        inp, out, svg = run_dir / "input.json", run_dir / "output.json", run_dir / "chart.svg"
        inp.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")
        args = [python or sys.executable, *PYTHON_FLAGS, str(spec.file), "--input-file", str(inp), "--output-file", str(out)]
        if spec.svg:
            args += ["--svg-out", str(svg)]
        t0 = time.monotonic()
        try:
            proc = subprocess.Popen(args, cwd=run_dir, env=child_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as err:
            return RunResult("spawn-error", f"启动失败：{err}")
        try:
            _, err_bytes = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            _, err_bytes = proc.communicate()
            return RunResult("timeout", f"超过 {timeout:g} 秒，已终止", duration_ms=_ms(t0), stderr=err_bytes.decode("utf-8", "replace")[-4000:])
        ms, stderr = _ms(t0), err_bytes.decode("utf-8", "replace")[-4000:]
        if proc.returncode != 0:
            return RunResult("exit", f"退出码 {proc.returncode}：{stderr.strip()[-500:]}", duration_ms=ms, stderr=stderr)
        try:
            output = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            return RunResult("invalid-output", f"输出不是 JSON：{err}", duration_ms=ms, stderr=stderr)
        if not isinstance(output, dict) or (missing := [k for k in REQUIRED_OUTPUT_KEYS if k not in output]):
            what = "不是 JSON 对象" if not isinstance(output, dict) else f"缺少 {', '.join(missing)}"
            return RunResult("schema", f"输出{what}", output=output if isinstance(output, dict) else None, duration_ms=ms, stderr=stderr)
        if not isinstance(output["warnings"], list):
            return RunResult("schema", "输出的 warnings 不是列表", output=output, duration_ms=ms, stderr=stderr)
        svg_text = None
        if spec.svg:
            svg_text = svg.read_text(encoding="utf-8") if svg.is_file() else ""
            if not svg_text.strip():
                return RunResult("schema", "登记了 svg，但没有生成风险图", output=output, duration_ms=ms, stderr=stderr)
        return RunResult("ok", output=output, duration_ms=ms, stderr=stderr, svg=svg_text)


def _kill(proc: subprocess.Popen) -> None:
    for sig, wait in ((signal.SIGTERM, 1.0), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


# ── 自检 ────────────────────────────────────────────────────────────────────


def allowed_imports(root: Path) -> set[str]:
    req = Path(root) / "requirements.txt"
    names: set[str] = set()
    if req.is_file():
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                dist = re.split(r"[<>=!~\[ ;]", line, maxsplit=1)[0].lower()
                names.add(_IMPORT_NAMES.get(dist, dist.replace("-", "_")))
    return names | set(sys.stdlib_module_names)


def check_imports(skill: Skill, allowed: set[str]) -> list[str]:
    problems = []
    local = {p.stem for p in (skill.dir / "scripts").glob("*.py")}
    for path in sorted((skill.dir / "scripts").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            for mod in mods:
                top = mod.split(".")[0]
                if top not in allowed and top not in local:
                    problems.append(f"{skill.name}/{path.relative_to(skill.dir)}:{node.lineno}：import {mod} 不在 requirements.txt 和标准库里")
    return problems


def check_references(skill: Skill) -> list[str]:
    problems = []
    for path in sorted((skill.dir / "references").glob("*.y*ml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        rel = f"{skill.name}/{path.relative_to(skill.dir)}"
        if not isinstance(data, dict) or data.get("status") not in ("placeholder", "reviewed"):
            problems.append(f"{rel}：顶层要有 status: placeholder 或 reviewed")
        elif data["status"] == "reviewed" and not data.get("reviewed_by"):
            problems.append(f"{rel}：status 为 reviewed 时要写 reviewed_by（审定人和日期）")
        if path.name == "collect.yaml":
            problems += check_collect_spec(path, rel)
    return problems


def check_collect_spec(path: Path, rel: str) -> list[str]:
    """采集清单：格式合法，select 用的角色都在契约的词表里（技能按角色取数，词表是各矿共同遵守的）。"""
    from .collect import parse_collect_spec
    from .models import SensorRole

    try:
        spec = parse_collect_spec(path.read_text(encoding="utf-8"), rel)
    except ValueError as err:
        return [str(err)]
    vocabulary = {r.value for r in SensorRole}
    problems = []
    for kind, k in spec["kinds"].items():
        for g in (k or {}).get("sensors") or []:
            role = g["select"].get("role")
            if role is not None and role not in vocabulary:
                problems.append(f"{rel}：{kind} 的测点组 {g['name']} 用了词表里没有的角色 {role}（词表：{sorted(vocabulary)}）")
    return problems


@dataclass
class CaseResult:
    skill: str
    script: str
    case: str
    ok: bool
    detail: str = ""
    duration_ms: int = 0


@dataclass
class CheckReport:
    skills: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and all(c.ok for c in self.cases)


def _first_difference(a: Any, b: Any, path: str = "$") -> str:
    if type(a) is not type(b):
        return f"{path}：期望 {json.dumps(b, ensure_ascii=False)[:80]}，实际 {json.dumps(a, ensure_ascii=False)[:80]}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                return f"{path}.{k}：缺少"
            if k not in b:
                return f"{path}.{k}：多出"
            if a[k] != b[k]:
                return _first_difference(a[k], b[k], f"{path}.{k}")
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}：长度期望 {len(b)}，实际 {len(a)}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            if x != y:
                return _first_difference(x, y, f"{path}[{i}]")
    return f"{path}：期望 {json.dumps(b, ensure_ascii=False)[:80]}，实际 {json.dumps(a, ensure_ascii=False)[:80]}"


def check(root: Path | None = None, *, python: str | None = None) -> CheckReport:
    root = Path(root or default_root())
    report = CheckReport()
    allowed = allowed_imports(root)
    for d in sorted(root.iterdir()):
        if not (d / "skill.yaml").is_file():
            continue
        try:
            skill = load_skill(d)
        except (SkillError, OSError, yaml.YAMLError, KeyError) as err:
            report.problems.append(f"{d.name}：{err}")
            continue
        report.skills.append(skill.name)
        report.problems += check_imports(skill, allowed)
        report.problems += check_references(skill)
        flow = (yaml.safe_load((skill.dir / "skill.yaml").read_text(encoding="utf-8")) or {}).get("kind") == "flow"
        if not skill.scripts and not flow:  # 流程型技能（kind: flow）只有 SKILL.md 和参数，没有脚本
            report.problems.append(f"{skill.name}：skill.yaml 没有登记脚本")
        cases_dir = skill.dir / "tests" / "cases"
        for name, spec in skill.scripts.items():
            inputs = sorted((cases_dir / name).glob("*.input.json"))
            if not inputs:
                report.problems.append(f"{skill.name}：脚本 {name} 没有自检样例（tests/cases/{name}/*.input.json）")
            for inp in inputs:
                case = inp.name.removesuffix(".input.json")
                exp_path = inp.with_name(f"{case}.expected.json")
                if not exp_path.is_file():
                    report.cases.append(CaseResult(skill.name, name, case, False, f"缺少 {exp_path.name}"))
                    continue
                r = run_script(spec, json.loads(inp.read_text(encoding="utf-8")), python=python)
                if not r.ok:
                    report.cases.append(CaseResult(skill.name, name, case, False, f"{r.status}：{r.detail}", r.duration_ms))
                    continue
                expected = json.loads(exp_path.read_text(encoding="utf-8"))
                ok = r.output == expected
                report.cases.append(CaseResult(skill.name, name, case, ok, "" if ok else _first_difference(r.output, expected), r.duration_ms))
        report.problems += check_templates(skill, report)
    for f in sorted(root.glob("*/tests/cases/*/*.expected.json")):
        if not f.with_name(f.name.replace(".expected.json", ".input.json")).is_file():
            report.problems.append(f"{f.relative_to(root)}：没有对应的 input.json")
    return report


def check_templates(skill: Skill, report: CheckReport) -> list[str]:
    """templates/<名>.md 的样例：tests/templates/<名>/<样例>.input.json（渲染数据）+ 同名 .expected.md，渲染结果整体相等。"""
    from .template import TemplateError, render

    problems = []
    for tpl in sorted((skill.dir / "templates").glob("*.md")):
        cases = sorted((skill.dir / "tests" / "templates" / tpl.stem).glob("*.input.json"))
        if not cases:
            problems.append(f"{skill.name}：模板 {tpl.name} 没有样例（tests/templates/{tpl.stem}/*.input.json）")
        for inp in cases:
            case = inp.name.removesuffix(".input.json")
            exp = inp.with_name(f"{case}.expected.md")
            name = f"templates/{tpl.name}"
            if not exp.is_file():
                report.cases.append(CaseResult(skill.name, name, case, False, f"缺少 {exp.name}"))
                continue
            try:
                got = render(tpl.read_text(encoding="utf-8"), json.loads(inp.read_text(encoding="utf-8")))
            except TemplateError as err:
                report.cases.append(CaseResult(skill.name, name, case, False, f"渲染失败：{err}"))
                continue
            want = exp.read_text(encoding="utf-8")
            detail = "" if got == want else _first_line_difference(got, want)
            report.cases.append(CaseResult(skill.name, name, case, got == want, detail))
    return problems


def _first_line_difference(got: str, want: str) -> str:
    g, w = got.split("\n"), want.split("\n")
    for i, (a, b) in enumerate(zip(g, w, strict=False)):
        if a != b:
            return f"第 {i + 1} 行：期望 {b[:60]!r}，实际 {a[:60]!r}"
    return f"行数期望 {len(w)}，实际 {len(g)}"


def _print_report(report: CheckReport) -> None:
    for c in report.cases:
        mark = "通过" if c.ok else "失败"
        print(f"{mark}  {c.skill}/{c.script}/{c.case}  {c.duration_ms} ms  {c.detail}")
    for p in report.problems:
        print(f"问题  {p}")
    passed = sum(c.ok for c in report.cases)
    print(f"{len(report.skills)} 个技能，样例 {passed}/{len(report.cases)} 通过，{len(report.problems)} 个问题")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rescue_contract.skills")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="自检全部第二版技能")
    c.add_argument("root", nargs="?", type=Path)
    c.add_argument("--python", help="运行脚本用的解释器（缺省为当前解释器）")
    r = sub.add_parser("run", help="运行一个脚本")
    r.add_argument("skill")
    r.add_argument("script")
    r.add_argument("--input-file", required=True, type=Path)
    r.add_argument("--output-file", type=Path)
    r.add_argument("--root", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "check":
        report = check(args.root, python=args.python)
        _print_report(report)
        return 0 if report.ok else 1
    skill = load_skill(Path(args.root or default_root()) / args.skill)
    if args.script not in skill.scripts:
        print(f"{args.skill} 没有登记脚本 {args.script}（有：{', '.join(skill.scripts)}）", file=sys.stderr)
        return 2
    res = run_script(skill.scripts[args.script], json.loads(args.input_file.read_text(encoding="utf-8")))
    if not res.ok:
        print(f"{res.status}：{res.detail}", file=sys.stderr)
        return 1
    text = json.dumps(res.output, ensure_ascii=False, indent=2) + "\n"
    if args.output_file:
        args.output_file.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
