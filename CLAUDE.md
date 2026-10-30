# CLAUDE.md

煤矿应急救援智能体：基于 DeepSeek Harness（DSH，锁定 0.2.1-rc.4）。一期闭环场景是采空区煤自燃，开发和演示都对着模拟矿井进行。

- 方案（为什么这样做）：`docs/implementation-plan.md`（主版本，带图快照见同名 PDF）
- 架构（怎么连起来）：`docs/architecture.md`；设计决策：`docs/decisions/`
- 真实 DSH 上的验证记录（含已知问题）：`docs/validation-checklist.md`

用中文写文档、注释、提交说明和面向用户的文字；标识符用英文。

## 常用命令

```bash
make setup-py                 # 只装 Python 部分（不需要 DSH）
make setup                    # 全部（需要 node 22+、pnpm、dsh 0.2.1-rc.4）
make test                     # Python + TS 测试
make lint                     # ruff
make check                    # 提交前：lint + 测试 + patch 离线校验 + 基线评测
make up / make down / make smoke   # 启停 6 个 MCP 服务并冒烟
make eval                     # 确定性基线评测（不经过大模型）
make eval-agent ARGS="--url '<DSH 地址带 token>' --model deepseek-official/deepseek-flash --repeat 3"   # 智能体端到端评测（需先 make dev）
make schema                   # 契约模型改动后重新导出 JSON Schema（CI 会检查）
python3 scripts/sim.py state|reset <场景>|advance <小时>|apply <处置>   # 模拟控制台
make dev                      # MCP 服务 + DSH 网页端（需要 DEEPSEEK_API_KEY）
```

单个包的测试：`cd mcp && uv run pytest fire-model/tests -q`。

## 结构与边界（改代码前先看）

- `mcp/contract/`：数据契约（pydantic）、红线引擎 `redlines.py`、方案校验 `validation.py`、`MineData` 客户端、一致性测试。**契约是各工作线之间的边界**，改 `models.py` 要谨慎并 `make schema`。
- `mcp/<服务>/`：6 个 MCP 服务，各自 `src/rescue_<名>/server.py` 里 `build_server()`，`__main__.py` 用 `rescue_contract.serving.serve` 启动（streamable-http，端口 8101–8106）。
- `dsh-bundle/`：DSH bundle。`src/approval`（红线预检 + 人工确认）、`src/audit`、`presets/*.patch.yml`（5 个 Agent）、`cordis.patch.yml`。对 DSH 类型的引用只放在 `src/adapter/dsh.ts`。
- `skills/<角色>/<技能>/SKILL.md`：给 Agent 的流程知识。草稿，待专家审定。
- `scenarios/*.yaml`：标准场景 = 演示脚本 = 评测集 = 回归集。
- `eval/baseline.py` + `expectations.yaml`：模型层回归基线。
- `eval/agent/`：智能体端到端评测。`cases.yaml` 用例，`run.py` 经 DSH 网页端（Playwright）提问，`grade.py` 读 `.dsh-home/sessions` 会话日志判定（可 `grade.py <会话 id>` 离线判定、`run.py --regrade` 按新规则重判）。

## 必须遵守的约定

1. **数值只来自代码计算**。Agent 提示词和技能里不写阈值、不让模型估算；新的判断逻辑写进 MCP 服务并加测试。计数、合计、差值、汇总结论也算：评测发现 Agent 会自己数人数、相减算升温、汇总多个测点的结论，这些都要由工具给出（如 `threatened_personnel` 的 count、`gas_trend` 的 change、`evaluate_effect_panel` 的 counts/overall）。
2. **只有 `rescue-commander` 挂载 actions**；其他预设只读。`scripts/check_patches.py` 会检查。
3. **红线规则只在 `rescue_contract/redlines.py` 实现一次**。新增规则：改规则表 + `contract/tests/test_redlines.py` + `skills/compliance/red-line-rules/SKILL.md`（有同步测试）。未知值按不通过处理（fail closed）。
4. **智能体看不到模拟器真值**。`/sim/state` 的 truth 只给控制台和评测；不要通过 MCP 工具暴露。
5. **mine-data 的工具集合就是真实矿井适配器要实现的接口**。增删工具要同步 `rescue_contract/conformance.py`。
6. 阈值（`fire-model/.../thresholds.yaml`）、场景参数、评测期望都是**开发占位值**。可以为了测试调整，但要在提交说明里写明，并且不要声称它们“正确”。
7. MCP Python SDK 是 2.x：`from mcp.server.mcpserver import MCPServer`（没有 FastMCP）。工具返回 pydantic 模型或 `dict[str, Any]`，不要返回裸 `dict`（会丢结构化输出）。输入错误抛 `ToolError`（`mcp.server.mcpserver.exceptions`）：其他异常的说明传不到 Agent（`mcp/tests/test_tool_errors.py`）。
8. DSH 版本锁定在 0.2.1-rc.4。DSH 的 `DSH_*` 环境变量必须 export，写在 `.env` 里不生效。
9. 不要改 `var/` 下的台账和审计日志；它们是复盘依据。

## 当前状态（2026-10-30）

- 已完成：契约（0.2.0，含火区台账）与红线引擎、模拟矿井（6 场景）、6 个服务、DSH bundle、技能草稿、基线评测、CI。
- 真实 DSH 0.2.1-rc.4 验证清单 17 项全部通过（`docs/validation-checklist.md`），`pnpm-lock.yaml` 已提交。其中 #15 的自动压缩未触发，靠手动 `/compact`；网页端权限选择器只保留审批为 ask 的预设。
- `fire_model` 除预警等级外，还给出自燃发展阶段（`assess_warning_level` 的 stage）和处置效果（`evaluate_effect_panel` 总体结论、`evaluate_effect` 单点）；判据在 `thresholds.yaml` 的 stage、effect 两节，均为占位值。
- 智能体端到端评测（DeepSeek deepseek-flash，各 3 次）：`s2-assess-1201` 全部通过 3/3，`s2-effect-after-inerting` 2/3（剩余问题是 Agent 偶尔自行推算时长或取整）。本地 qwen35-122b-a10b 不适合担任救援 Agent（不委派、编造阈值），不再纳入评测。
- 待办：专家审定阈值（含阶段、效果判据）/场景/技能；评测补充用例（升级应急响应、封闭方案、人工确认被拒）；ventnet 的 Hardy Cross 风网解算；确认人按角色限定；研判预设未挂载 kb，无法照抄规程条文（其预设提示词却要求引用）；kb 尚未接入 RAGFlow，目前只有本地降级。
