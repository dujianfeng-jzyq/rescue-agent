"""生成 rescue-mine 接口说明（docs/rescue-mine-interface.md）。

内容来自两处：接口表（rescue_contract.interface：分组、读写、可选功能）和实现该接口的服务实际对外的工具定义
（工具说明、参数、返回结构）。文档由代码生成，测试检查它与代码一致（改了工具或模型要重新生成）。

用 render(tools) 生成；tools 是 MCP 的 list_tools 结果。命令行见 scripts/make_interface_doc.py（用模拟矿井生成）。
"""

from __future__ import annotations

from typing import Any

from .interface import INTERFACE_VERSION, REQUIRED_TOOLS, TOOLS, WRITE_TOOLS, ToolSpec
from .models import CONTRACT_VERSION, SENSOR_ROLE_INFO

_NULL = {"type": "null"}


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def type_of(schema: dict[str, Any], defs: dict[str, Any]) -> str:
    """JSON Schema → 简短的类型写法：string、number、list[Reading]、Measurand、T | null。"""
    if "$ref" in schema:
        return _ref_name(schema["$ref"])
    if "anyOf" in schema or "oneOf" in schema:
        parts = schema.get("anyOf") or schema.get("oneOf")
        non_null = [p for p in parts if p != _NULL]
        text = " | ".join(type_of(p, defs) for p in non_null)
        return f"{text} | null" if len(non_null) != len(parts) else text
    if "enum" in schema:
        return "枚举(" + " / ".join(str(v) for v in schema["enum"]) + ")"
    t = schema.get("type")
    if t == "array":
        return f"list[{type_of(schema.get('items', {}), defs)}]"
    if t == "object":
        if "properties" in schema and schema.get("title"):
            return str(schema["title"])  # 内联的模型（工具直接返回一个模型时）：用模型名
        if "additionalProperties" in schema and isinstance(schema["additionalProperties"], dict):
            return f"map[string, {type_of(schema['additionalProperties'], defs)}]"
        return "object"
    if schema.get("pattern", "").startswith(r"^\d{4}-\d{2}-\d{2}T"):
        return "MineTime"
    return str(t) if t else "any"


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split()).replace("|", "\\|")


def _unwrap_output(out: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """工具返回列表时 SDK 包一层 {result: [...]}：还原成 (返回类型的 schema, defs)。"""
    if not out:
        return None, {}
    defs = out.get("$defs", {})
    props = out.get("properties", {})
    if set(props) == {"result"} and out.get("x-fastmcp-wrap-result", True) and "required" in out:
        return props["result"], defs
    return out, defs


def _fields(model: dict[str, Any], defs: dict[str, Any]) -> list[str]:
    required = set(model.get("required", []))
    rows = []
    for name, sub in model.get("properties", {}).items():
        default = "" if name in required else (f"，缺省 {sub['default']!r}" if "default" in sub else "，可省略")
        desc = _one_line(sub.get("description"))
        rows.append(f"| `{name}` | {type_of(sub, defs).replace('|', chr(92) + '|')} | {'是' if name in required else '否'}{default} | {desc} |")
    return rows


def render(tools: list[Any]) -> str:
    by_name = {t.name: t for t in tools}
    lines: list[str] = []
    w = lines.append

    w("# rescue-mine 接口说明")
    w("")
    w(f"> 接口版本 **{INTERFACE_VERSION}**，数据契约 **{CONTRACT_VERSION}**。**本文件由代码生成**（`make interface-doc`），不要手改；")
    w("> 测试会检查它与代码一致。设计依据见 `docs/design-v3.md` 第 4 节。")
    w("")
    w("rescue-mine 是 rescue-agent 与矿井之间的**唯一接口**，也是**唯一随矿井变化的部分**：开发阶段由模拟矿井（mine-sim）实现，")
    w("产品阶段由真实矿井适配器实现。对接新矿井只需要实现本接口，rescue-agent 的技能、插件和判据不改。")
    w("")
    w("## 边界规则")
    w("")
    w("- rescue-mine **只有“读数据”和“执行动作”，不含策略**：不做红线校验、不做研判、不存指令台账。这些都在 rescue-agent 一侧（guard）。")
    w("- 写入类工具（见下）只有 guard 调用；智能体读矿井只经证据插件（留记录），写矿井只经 guard。")
    w("- 模拟器的真值、读数设置、倍速、事故注入是 mine-sim 的控制接口（`/sim/`），**不属于本接口**，智能体看不到。")
    w("- 所有时间是矿井本地时间（`MineTime`，ISO 8601，不带时区）；“现在”以 `get_clock` 为准，不用系统时间。")
    w("- 输入错误必须以工具错误返回并写明原因（SDK 里抛 `ToolError`）；数据质量为 `missing`、`suspect` 的读数不能当依据。")
    w("")
    w("## 认证")
    w("")
    w("Streamable HTTP + 静态 Bearer 令牌。读取工具认 `RESCUE_MCP_MINE_DATA_TOKEN`（没有则 `RESCUE_MCP_TOKEN`）；")
    w("**写入类工具**另认写入令牌 `RESCUE_MCP_MINE_ACT_TOKEN`（只有 guard 持有，读令牌调用写入工具返回 401）。")
    w("写入令牌也可以做普通读取（guard 要回读回执）。适配器应实现同样的读写分离。")
    w("")
    w("## 工具总览")
    w("")
    w("“功能”列为空表示**必需**；否则是可选功能，矿井不提供时对应工具可以不实现，由 `get_capabilities` 如实说明。")
    w("")
    w("| 分组 | 工具 | 读/写 | 功能 | 说明 |")
    w("| --- | --- | --- | --- | --- |")
    for t in TOOLS:
        feat = "" if t.name in REQUIRED_TOOLS else (f"`{t.feature}`" if t.feature != "action_status" else "任一通知/指令功能")
        w(f"| {t.group} | `{t.name}` | {'**写**' if t.name in WRITE_TOOLS else '读'} | {feat} | {t.summary} |")
    w("")
    w("## 语义角色词表")
    w("")
    w("技能按**角色**取数，不按测点编号：`list_sensors` 里每个测点带 `role`（和所属工作面 `panel`），采集清单写 `select: {role: upper_corner, panel: true}`。")
    w("对接新矿井时，适配器把该矿的测点映射到下面的角色；词表里没有的先用 `other`，并提给我们补充。")
    w("**词表是占位的**（演示后由专家补全）。")
    w("")
    w("| 角色 | 含义 |")
    w("| --- | --- |")
    for role, desc in SENSOR_ROLE_INFO.items():
        w(f"| `{role.value}` | {desc} |")
    w("")
    w("## 工具详情")
    models: dict[str, dict[str, Any]] = {}
    group = None
    spec: ToolSpec
    for spec in TOOLS:
        t = by_name.get(spec.name)
        if t is None:
            continue
        if spec.group != group:
            group = spec.group
            w("")
            w(f"### {group}")
        w("")
        w(f"#### `{spec.name}`{'（写入）' if spec.name in WRITE_TOOLS else ''}")
        w("")
        w(_one_line(t.description) or spec.summary)
        w("")
        inp = t.input_schema or {}
        idefs = inp.get("$defs", {})
        props = inp.get("properties", {})
        if props:
            w("参数：")
            w("")
            w("| 名称 | 类型 | 必填 | 说明 |")
            w("| --- | --- | --- | --- |")
            req = set(inp.get("required", []))
            for name, sub in props.items():
                default = "" if name in req else (f"，缺省 {sub['default']!r}" if "default" in sub else "，可省略")
                w(f"| `{name}` | {type_of(sub, idefs).replace('|', chr(92) + '|')} | {'是' if name in req else '否'}{default} | {_one_line(sub.get('description'))} |")
            models.update({k: v for k, v in idefs.items()})
        else:
            w("无参数。")
        out, odefs = _unwrap_output(t.output_schema)
        if out is not None:
            w("")
            w(f"返回：`{type_of(out, odefs)}`")
            models.update(odefs)
            if "properties" in out and "$ref" not in out:
                models[out.get("title", spec.name)] = out
    w("")
    w("## 数据结构")
    w("")
    w("以上工具用到的结构（字段后“可省略”表示有缺省值）。完整 JSON Schema 在 `mcp/contract/schema/`（`make schema` 导出）。")
    for name in sorted(models):
        m = models[name]
        w("")
        w(f"### {name}")
        if m.get("description"):
            w("")
            w(_one_line(m["description"]))
        w("")
        if "enum" in m:
            w("枚举值：" + "、".join(f"`{v}`" for v in m["enum"]))
        elif "properties" in m:
            w("| 字段 | 类型 | 必填 | 说明 |")
            w("| --- | --- | --- | --- |")
            lines.extend(_fields(m, models))
    w("")
    w("## 对接新矿井")
    w("")
    w("1. 实现“必需”工具，再按矿井实际能力实现可选功能；`get_capabilities` 如实列出 `tools` 和 `features`。")
    w("2. `describe_mine` 如实回答工作面拓扑；`list_sensors` 给每个测点标 `panel` 和 `role`。")
    w("3. 写入类工具只执行、不判断；返回回执，异步生效的用 `get_action_status` 查询；相同 `idempotency_key` 重复提交只执行一次。")
    w("4. 运行一致性检查：`python -m rescue_contract.conformance <URL>`。写入类检查会真的执行动作，**只对模拟矿井或测试环境加 `--with-actions`**。")
    w("5. 一致性检查通过后，把 `RESCUE_MCP_MINE_DATA_URL` 指向适配器；rescue-agent 一侧不用改。")
    w("")
    return "\n".join(lines)
