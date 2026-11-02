# CLAUDE.md

煤矿应急救援智能体：基于 DeepSeek Harness（DSH，锁定 0.2.1-rc.4）。一期闭环场景是采空区煤自燃，开发和演示都对着模拟矿井进行。

- 方案（为什么这样做）：`docs/implementation-plan.md`（主版本，带图快照见同名 PDF）
- 架构（怎么连起来）：`docs/architecture.md`；设计决策：`docs/decisions/`
- 真实 DSH 上的验证记录（含已知问题）：`docs/validation-checklist.md`
- 工作流与智能体分工（正在按此实施）：`docs/workflow-design.md`，决策见 `docs/decisions/0006-workflow-vs-agent.md`

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
make eval-agent ARGS="--url '<DSH 地址带 token>' --model deepseek-official/deepseek-flash --repeat 3"   # 智能体端到端评测（需先 make dev-eval）
make dev-eval                 # 同 make dev，但指令台账和审计写到 var/eval/（评测用例会放行指令）
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
- `eval/agent/`：智能体端到端评测。`cases.yaml` 用例（`approvals` 指定人工确认时放行哪些指令，其余拒绝），`run.py` 经 DSH 网页端（Playwright）提问并点确认，`grade.py` 读 `.dsh-home/sessions` 会话日志判定（可 `grade.py <会话 id>` 离线判定、`run.py --regrade` 按新规则重判）。

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

## 当前状态（2026-11-01）

- 已完成：契约（0.4.0，含火区台账、束管 H2；`get_personnel` 返回人数汇总）与红线引擎、模拟矿井（6 场景）、6 个服务、DSH bundle、技能草稿、基线评测、CI。
- 真实 DSH 0.2.1-rc.4 验证清单 #1–#17 全部通过（`docs/validation-checklist.md`），`pnpm-lock.yaml` 已提交。其中 #15 的自动压缩未触发，靠手动 `/compact`；网页端权限选择器只保留审批为 ask 的预设。#18（DSH 自带工作流）评估后不采用：子 Agent 不能指定预设、拿不到 kb，调 actions 会被自动拒绝。
- `fire_model` 除预警等级外，还给出自燃发展阶段（`assess_warning_level` 的 stage）和处置效果（`evaluate_effect_panel` 总体结论、`evaluate_effect` 单点）；判据在 `thresholds.yaml` 的 stage、effect 两节，均为占位值。
- 智能体端到端评测（DeepSeek deepseek-flash，各 3 次，2026-10-30）：`s2-assess-1201` 2/3、`s2-effect-after-inerting` 3/3、`s4-escalate-1201` 1/3。剩余问题：Agent 自己数自己列出的条文或事项（“上述 7 条”“4 项”），自己归纳界限（“已超 31 ppm”），偶尔中途向人提问而不收尾。
  `s4-warning-rejected`（拒绝确认）：弹确认、被拒后不重复提交、如实说明均 3/3，全部通过 0/3（数值溯源）；`s4-sealing-plan`（放行封闭方案）：dispatch 补工具后 3/3 走通发布（确认放行、回执 issued），全部通过 0/3，剩余为 Agent 自己数条数、归纳界限（“低于 12%”）和笔误（1201 写成 1210）。
  `dispatch` 的缺口（check_supply、estimate_inert_gas 的 supply）、路程（route）、密闭材料（estimate_seal_materials）、注浆量（estimate_grout）由代码计算，参数在 `dispatch/.../params.yaml`，均为占位值。
  `assess_explosion_risk` 按束管测点做多组分（含 H2，契约 0.4.0 新增）、温度补偿的爆炸三角形（来自 explosion-triangle-synthesis 技能，分区判据已重新设计），`assess_explosion_risk_sample` 用于人工化验单；参数在 `fire-model/.../explosion.yaml`，为占位值。每次计算生成风险图（SVG）和 JSON（`var/charts/`），结果里的 `chart.markdown` 放进回答后网页端显示图片（经 DSH 的 /api/file），下载走 fire_model 的 `/charts/`（凭随机文件名，不校验令牌）。评测用例 `s4-explosion-risk`。本地 qwen35-122b-a10b 不适合担任救援 Agent（不委派、编造阈值），不再纳入评测。
- kb 已接入 RAGFlow，用演示专用的三个 dataset（应急救援-法规/预案/案例-演示，laws 切法，迁移方案见 `docs/kb-migration.md`，id 见 `.env`）；RAGFlow 不可用或断开时重试，仍失败则降级到本地红线条文。`get_article` 按条号数值在文档切片里直接查找，RAGFlow 频繁断开连接（疑似网关约 5 秒超时），单次查条 15–65 秒。`s4-escalate-1201`（2026-11-02 演示库，总指挥“每次回答自成完整结论”之后，3 次）：stage_reported、kb_cited 均 3/3；结束的 2 次中全部通过 1/2，另 1 次各项都通过但超过 900 秒（RAGFlow 查条慢，正常结束的也要 217–893 秒）；剩余为 Agent 自己数条数（“1–12 项”）、点名未调用的 evaluate_effect_panel。
- 进行中：按 `docs/workflow-design.md` 把研判、效果评估、升级建议、方案现场条件改为代码工作流（新服务 `mcp/workflow`，端口 8107），从 P1 开始。
- 待办：专家审定阈值（含阶段、效果判据）/场景/技能；专家审定 dispatch 用量参数；专家审定爆炸三角形参数和分区判据；评测运行器按用例预设回答 Agent 的提问；ventnet 的 Hardy Cross 风网解算；确认人按角色限定；排查 RAGFlow 断开连接（网关超时）。
