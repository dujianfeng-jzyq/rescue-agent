# CLAUDE.md

煤矿应急救援智能体：基于 DeepSeek Harness（DSH，锁定 0.2.1-rc.4）。一期闭环场景是采空区煤自燃，开发和演示都对着模拟矿井进行。

- 方案（为什么这样做）：`docs/implementation-plan.md`（主版本；同名 PDF 是第一版的带图快照）
- **第三版设计（草稿，`v3` 分支）**：`docs/design-v3.md`（rescue-agent + rescue-mine、常驻监听与事件、专家型智能体）
- **第二版设计（实施中，`v2` 分支）**：`docs/design-v2.md`，决策见 `docs/decisions/0007-skills-plugins-fixed-flows.md`
- 架构（怎么连起来）：`docs/architecture.md`（第二版；第 8 节是过渡期）；设计决策：`docs/decisions/`
- 真实 DSH 上的验证记录（含已知问题）：`docs/validation-checklist.md`
- 第一版的工作流与智能体分工（P4 后停用）：`docs/workflow-design.md`，决策见 ADR 0006

用中文写文档、注释、提交说明和面向用户的文字；标识符用英文。

## 常用命令

```bash
make setup-py                 # 只装 Python 部分（不需要 DSH）
make setup                    # 全部（需要 node 22+、pnpm、dsh 0.2.1-rc.4）
make test                     # Python + TS 测试
make lint                     # ruff
make check                    # 提交前：lint + 测试 + patch 离线校验 + 基线评测
make up / make down / make smoke   # 启停 MCP 服务（mine_data、actions）并冒烟
make eval                     # 确定性基线评测（不经过大模型，运行 fire-assessment 的技能脚本）
make eval-agent ARGS="--url '<DSH 地址带 token>' --model deepseek-official/deepseek-flash --repeat 3"   # 智能体端到端评测（需先 make dev-eval）
make dev-eval                 # 同 make dev，但指令台账、研判快照、值守事件和审计写到 var/eval/（评测用例会放行指令）
make schema                   # 契约模型改动后重新导出 JSON Schema（CI 会检查）
python3 scripts/sim.py state|reset <场景>|advance <小时>|apply <处置>   # 模拟控制台
make dev                      # MCP 服务 + DSH 网页端（需要 DEEPSEEK_API_KEY）
make demo-up / demo-down / demo-reset   # 演示环境（Docker）：网页端 13080、控制台 13090/console，数据在 var/demo/，令牌在 var/demo/tokens.env
make skills-check             # 第二版技能自检（结构、import、样例）
python -m rescue_contract.collect <collect.yaml> <kind> <panel>   # 按采集清单取一次证据（Python 采集小工具）
```

单个包的测试：`cd mcp && uv run pytest contract/tests -q`（包：contract、mine-data-sim、actions）。

## 结构与边界（改代码前先看）

第二版的结构（ADR 0007，`docs/design-v2.md`）：

- `skills/<技能名>/`：专家经验和算法的唯一来源。SKILL.md（流程）、`scripts/`（纯函数，`--input-file`/`--output-file`，不访问网络和凭据）、`templates/`、`references/`（参数，标注占位值）、`tests/cases/`（自检样例，CI 运行）。
- `dsh-bundle/src/evidence/`（证据插件：采集、`runScript`、记录、`check_mine`）、`src/fire-loop/`（固定流程）、`src/knowledge/`（知识库插件、条文索引）、`src/approval`（红线预检 + 人工确认）、`src/audit`；`presets/commander.patch.yml`（唯一主预设）、`cordis.patch.yml`。插件只做编排，不写领域判据。对 DSH 类型的引用只放在 `src/adapter/dsh.ts`。
- 记录（`Record`，契约 0.9.0）：只有 DSH 进程（证据插件）能写，bash 沙箱和 actions 只读；指令引用 `record_id`。
- `mcp/contract/`：数据契约（pydantic）、红线引擎 `redlines.py`、方案校验 `validation.py`、`MineData` 客户端、一致性测试、技能自检（`rescue_contract.skills`）、模板与规范化 JSON。**契约是各工作线之间的边界**，改 `models.py` 要谨慎并 `make schema`。
- `mcp/mine-data-sim`（模拟矿井，含演示控制台）、`mcp/actions`（指令出口与台账）：各自 `src/rescue_<名>/server.py` 里 `build_server()`，用 `rescue_contract.serving.serve` 启动（streamable-http，端口 8101、8106）。
- `scenarios/*.yaml`：标准场景 = 演示脚本 = 评测集 = 回归集。
- `eval/baseline.py` + `expectations.yaml`：确定性回归基线（运行技能脚本）。
- `eval/agent/`：智能体端到端评测。`cases.yaml` 用例（`approvals` 指定人工确认时放行哪些指令，其余拒绝），`run.py` 经 DSH 网页端（Playwright）提问并点确认，`grade.py` 读 `.dsh-home/sessions` 会话日志判定（可 `grade.py <会话 id>` 离线判定、`run.py --regrade` 按新规则重判）。
- 第一版的 Python 服务（ventnet、fire-model、dispatch、kb、workflow）已于 2026-11-04 从工作树删除，见 `docs/architecture.md` 第 8 节；契约里的研判快照和 `grade.py` 的第一版兼容留待第三版清理。


## 必须遵守的约定

1. **数值只来自代码计算**。“代码”指 MCP 服务和技能脚本（第二版起以技能脚本为准）。Agent 提示词和 SKILL.md 的正文里不写阈值、不让模型估算；新的判断逻辑写进技能脚本（或迁移完成前的 MCP 服务）并加自检样例或测试。计数、合计、差值、汇总结论也算：评测发现 Agent 会自己数人数、相减算升温、汇总多个测点的结论，这些都要由工具给出（如 `threatened_personnel` 的 count、`gas_trend` 的 change、`evaluate_effect_panel` 的 counts/overall）。智能体在沙箱里自己运行脚本的结果只作参考，不能被指令引用。
2. **只有 `rescue-commander` 挂载 actions**；其他预设只读（第二版只有这一个预设，固定流程的阶段子 Agent 用 `tools.guard` 限制为只能提交结果）。`scripts/check_patches.py` 会检查。指令必须引用研判快照（`snapshot_id`，第二版改为记录 `record_id`），现场条件、预警等级按快照或记录核对（`rescue_contract/validation.py`），不能由模型填写。
3. **红线规则只在 `rescue_contract/redlines.py` 实现一次**（第二版不迁入技能）。新增规则：改规则表 + `contract/tests/test_redlines.py` + `skills/compliance/red-line-rules/SKILL.md`（有同步测试；第二版是 `plan-compliance` 技能）。知识库插件需要的条号由规则表导出，不在 TS 里再写一遍。未知值按不通过处理（fail closed）。
4. **智能体看不到模拟器真值**。`/sim/state` 的 truth、读数设置、倍速只给控制台和评测；不要通过 MCP 工具或证据插件暴露。DSH 不持有 `/sim` 控制令牌。
5. **mine-data 的工具集合就是真实矿井适配器要实现的接口**。增删工具要同步 `rescue_contract/conformance.py`；第二版的采集清单（技能的 `references/collect.yaml`）只能用这些工具。
6. 阈值（`fire-model/.../thresholds.yaml`）、场景参数、评测期望都是**开发占位值**。可以为了测试调整，但要在提交说明里写明，并且不要声称它们“正确”。
7. MCP Python SDK 是 2.x：`from mcp.server.mcpserver import MCPServer`（没有 FastMCP）。工具返回 pydantic 模型或 `dict[str, Any]`，不要返回裸 `dict`（会丢结构化输出）。输入错误抛 `ToolError`（`mcp.server.mcpserver.exceptions`）：其他异常的说明传不到 Agent（`mcp/tests/test_tool_errors.py`）。
8. DSH 版本锁定在 0.2.1-rc.4。DSH 的 `DSH_*` 环境变量必须 export，写在 `.env` 里不生效。
9. 不要改 `var/` 下的台账和审计日志；它们是复盘依据。

## 第一版历史记录（2026-11-01，代码已删除，仅供查阅；现状看下面“第二版进度”）

- 已完成：契约（0.4.0，含火区台账、束管 H2；`get_personnel` 返回人数汇总）与红线引擎、模拟矿井（6 场景）、6 个服务、DSH bundle、技能草稿、基线评测、CI。
- 真实 DSH 0.2.1-rc.4 验证清单 #1–#17 全部通过（`docs/validation-checklist.md`），`pnpm-lock.yaml` 已提交。其中 #15 的自动压缩未触发，靠手动 `/compact`；网页端权限选择器只保留审批为 ask 的预设。#18（DSH 自带工作流）评估后不采用：子 Agent 不能指定预设、拿不到 kb，调 actions 会被自动拒绝。
- `fire_model` 除预警等级外，还给出自燃发展阶段（`assess_warning_level` 的 stage）和处置效果（`evaluate_effect_panel` 总体结论、`evaluate_effect` 单点）；判据在 `thresholds.yaml` 的 stage、effect 两节，均为占位值。
- 智能体端到端评测（DeepSeek deepseek-flash，各 3 次，2026-10-30）：`s2-assess-1201` 2/3、`s2-effect-after-inerting` 3/3、`s4-escalate-1201` 1/3。剩余问题：Agent 自己数自己列出的条文或事项（“上述 7 条”“4 项”），自己归纳界限（“已超 31 ppm”），偶尔中途向人提问而不收尾。
  `s4-warning-rejected`（拒绝确认）：弹确认、被拒后不重复提交、如实说明均 3/3，全部通过 0/3（数值溯源）；`s4-sealing-plan`（放行封闭方案）：dispatch 补工具后 3/3 走通发布（确认放行、回执 issued），全部通过 0/3，剩余为 Agent 自己数条数、归纳界限（“低于 12%”）和笔误（1201 写成 1210）。
  `dispatch` 的缺口（check_supply、estimate_inert_gas 的 supply）、路程（route）、密闭材料（estimate_seal_materials）、注浆量（estimate_grout）由代码计算，参数在 `dispatch/.../params.yaml`，均为占位值。
  `assess_explosion_risk` 按束管测点做多组分（含 H2，契约 0.4.0 新增）、温度补偿的爆炸三角形（来自 explosion-triangle-synthesis 技能，分区判据已重新设计），`assess_explosion_risk_sample` 用于人工化验单；参数在 `fire-model/.../explosion.yaml`，为占位值。每次计算生成风险图（SVG）和 JSON（`var/charts/`），结果里的 `chart.markdown` 放进回答后网页端显示图片（经 DSH 的 /api/file），下载走 fire_model 的 `/charts/`（凭随机文件名，不校验令牌）。评测用例 `s4-explosion-risk`。本地 qwen35-122b-a10b 不适合担任救援 Agent（不委派、编造阈值），不再纳入评测。
- kb 已接入 RAGFlow，用演示专用的三个 dataset（应急救援-法规/预案/案例-演示，laws 切法，迁移方案见 `docs/kb-migration.md`，id 见 `.env`）；RAGFlow 不可用或断开时重试，仍失败则降级到本地红线条文。`get_article` 按条号数值在文档切片里直接查找，RAGFlow 频繁断开连接（疑似网关约 5 秒超时），单次查条 15–65 秒。`s4-escalate-1201`（2026-11-02 演示库，总指挥“每次回答自成完整结论”之后，3 次）：stage_reported、kb_cited 均 3/3；结束的 2 次中全部通过 1/2，另 1 次各项都通过但超过 900 秒（RAGFlow 查条慢，正常结束的也要 217–893 秒）；剩余为 Agent 自己数条数（“1–12 项”）、点名未调用的 evaluate_effect_panel。
- 工作流改造 P1–P5 已完成（2026-11-02，分支 workflow，`docs/workflow-design.md`）：workflow 服务 7 个工具（assess_panel、review_effect、recommend_escalation、draft_plan、draft_dispatch_for_plan、get_monitor_events、review_timeline）+ 值守进程；契约 0.6.0（研判快照、`snapshot_id`、`HumanInput`），方案现场条件、预警等级、升级、调度单在 actions/预检/kb 自查三处按快照核对；预设减为 3 个（移除 rescue-assessment、rescue-dispatch）。
  智能体评测（deepseek-flash，各 3 次，按当前判定规则）：`s2-assess-1201`、`s2-effect-after-inerting`、`s4-escalate-1201`、`s4-explosion-risk`、`s4-warning-rejected`、`s4-plan-context-tamper` 均 3/3；研判类每次约 5–7 万 token、40–100 秒（改造前约 80 万 token、`s4-escalate` 217–893 秒）。
  `s4-sealing-plan`：快照引用、人工确认、发布在走通的运行里都成立，但全部通过不稳定（最新 0/2 + 1 次接口错误）：合规 Agent 凭记忆引用未检索的条文（“细则97(三)”）、合规判“不通过”后总指挥向人请示而评测运行器不会代答；单次 170–340 万 token，偏高。
- 待办：封闭方案流程的 token 消耗（总指挥上下文里反复携带完整方案 JSON）；合规 Agent 只引用检索到的条文；专家审定 workflow 的占位判据（升级、甲烷上升、爆炸危险性汇总、必备步骤、值守周期）；专家审定阈值（含阶段、效果判据）/场景/技能；专家审定 dispatch 用量参数；专家审定爆炸三角形参数和分区判据；评测运行器按用例预设回答 Agent 的提问；ventnet 的 Hardy Cross 风网解算；确认人按角色限定；排查 RAGFlow 断开连接（网关超时）。

## 第二版进度（`v2` 分支）

- 2026-11-03：第二版设计定稿（`docs/design-v2.md` 第五稿），ADR 0007；验证清单 #20（插件登记后台任务）、#22（插件起 Python 子进程）、#23（唤醒失败的补救）通过，#21（容器内的 bash 沙箱）待 P0 验证。实验 0001（技能驱动 vs 代码工作流）封存。验证代码在 `spike/job-watch`、`spike/plugin-subprocess` 分支，不合入。
- 技能脚本由插件起的子进程不在沙箱里、默认继承 DSH 进程的密钥：`runScript` 必须按白名单传环境变量、设超时；脚本是受信代码（#22）。会话工作区所有会话共用一个目录，导出按会话分子目录。
- provider 重试要把 `STREAM_CLOSED` 加进 `retryPolicy.retryableCodes`（默认不重试，#23）。
- P0（2026-11-03）：契约 0.7.0（`Record` 等，`snapshot_id` 的改名留到 P2）；规范化 JSON（RFC 8785）TS/Python 两份实现、共用样例；技能规范 `skills/README.md` 与自检 `make skills-check`；证据插件骨架 `dsh-bundle/src/evidence/`（MCP 客户端、记录、runScript、采集、get_record、export_record）与 Python 采集小工具 `rescue_contract.collect`（一致性测试逐字节相同）；令牌拆分（`RESCUE_MCP_<服务>_TOKEN`、`RESCUE_SIM_CONTROL_TOKEN`，退回 `RESCUE_MCP_TOKEN`）；主预设草稿 `profiles/v2.patch.yml`（`scripts/dev.sh --v2`）；provider 重试加 `STREAM_CLOSED`；#21 通过（容器里 Docker 默认配置下 DSH 用 Landlock，权限与本机一致）。P0 完成。
- 预设里提供 cordis 服务的插件必须放进带 `isolate` 的 `cordis:group`（否则新建会话失败）；固定流程插件放进同一组才能注入 `rescueEvidence`。
- 技能脚本用 `python -E -s -B -X utf8` 运行，不用 `-I`（不把脚本目录加进 sys.path）；`-E` 忽略 PYTHON* 环境变量。
- P1（2026-11-03）：技能 `fire-assessment`（check、warning、stage、alarms、trend、personnel）、`explosion-triangle`、`emergency-escalation`，由 fire_model、ventnet、workflow 迁移，判据和数值未改；与第一版服务对 6 个场景 16 个关键时刻一致（`mcp/workflow/tests/test_skill_parity.py`）。报告模板是 Mustache 子集（`rescue_contract.template` 与 `src/evidence/template.ts`，共用样例），证据插件 `renderReport`、`check_mine(once)`，固定流程插件 `src/fire-loop`（`run_fire_assessment`），TS 流程与 Python 逐条记录一致（`test_fire_loop_parity.py`）。样例由 `scripts/make_skill_cases.py` 生成。评测（`scripts/dev.sh --v2`，deepseek-flash，各 3 次）：`s2-assess-1201`、`s4-escalate-1201`、`s4-explosion-risk` 均 3/3，每次 4.4–6.5 万 token、41–59 秒。`grade.py` 认 `run_fire_assessment`。升级建议的条文暂用技能 references 的本地留存（P2 改为知识库）。
- 技能辅助模块不要与标准库内建模块同名（`_io` 会被 CPython 内建模块遮住）。
- P1 研判说明（2026-11-03）：`explanation_options.py` 按结果列出关注点、`references/explanation.yaml` 列下一步；阶段子 Agent（`startActivation`，与总指挥同一组合，`tools.guard` 只放行 `structured_output`）只选编号、写短说明，代码核对编号、条数、字数和数值（`src/fire-loop/explanation.ts`），不合格时报告只含事实部分并注明“研判说明未生成”。报告 .md 和风险图经 `present` 交付；**不用 `concludeTurn`**（rc.4 网页端看不到报告，验证清单“已知需要关注的点”）。主预设贴报告后不再另写分析。评测（各 3 次）：`s2-assess-1201`、`s4-escalate-1201`、`s4-explosion-risk` 均 3/3，研判说明 9/9 生成；每次 5.9–6.5 万 token（含阶段约 1.7 万）。`grade.py`：展示类检查看最后一轮全部文字、链接 `](<路径>)` 视同原样，新增 `explanation_generated`。
- 模板按 Markdown 排版：相邻的普通行会被并成一段，块之间要空一行，列表放在区块标签里保持紧凑。
- P2（2026-11-03）：条文索引——`scripts/kb/fetch_chunks.py` 取 RAGFlow 切片到 `var/kb-raw/`，`scripts/kb/build_article_index.py` 按条切分生成 `dsh-bundle/data/article-index.json`（10 个规程类文档 1536 条，条号全部连续；标准、要点只走检索）。laws 切法的切片顺序不一定是正文顺序，按每组第一条的条号重排；条文标题要求条号连续。kb 的 `local_articles.yaml` 是摘要不是原文，P2 起查条以索引为准。知识库插件 `src/knowledge`（`kb_get_articles`、`kb_search`、`kb_list_datasets`，服务 `rescueKnowledge`）；升级建议的条文原文由流程从索引取。契约 0.8.0：`WarningNotice`、`Escalation` 加 `record_id`（与 `snapshot_id` 二选一），按记录逐项核对，预检附 `ApprovalSummary`，审批弹窗按它列出；actions 只读记录目录，新增只读 `precheck_plan`（审批插件按 `precheck_` 前缀放行）；`make schema` 导出 `redline-articles.json`。`make dev-eval ARGS=--v2`。评测：v2 的 `s4-warning-rejected` 3/3（弹窗列出引用记录和核对项）、`s4-escalate-1201` 3/3（约 8 万 token，条文原文变长）；`s4-plan-context-tamper` 要 `draft_plan`，P3 前只能在第一版预设上跑：2/3，篡改 3/3 未发布，失败的 1 次是合规 Agent 写“RL-057(53)”被判数值无依据（第一版判定的边界情况）。
- P3（2026-11-03）：技能 `sealing-plan`（skeleton、compose，材料算法由 dispatch 迁移）、`plan-compliance`（scope、review）；固定流程 `draft_sealing_plan`（阶段“方案编写”“合规审核”，最多 2 轮，红线预检调 actions 的 `precheck_plan(redlines_only=true)`）；契约 0.9.0（`Plan.record_id`、`Citation.source` 加 index）；`issue_sealing_plan(record_id)` 只提交审核记录编号，actions 从记录取方案、核对审核结论和方案记录后强制红线校验。TS 流程与 Python 逐条记录一致（`test_fire_loop_parity.py`，命令行 `tools/sealing-plan.ts` 用写死的阶段回答）。`s4-sealing-plan` 去掉 `delegates_to`、加 `report_pasted`。评测（`make dev-eval ARGS=--v2`，deepseek-flash，3 次）：3/3 全部通过，每次 1 轮审核通过、确认放行、回执 issued，约 12 万 token（总指挥约 8 万 + 两个阶段各约 2 万）、66–72 秒（第一版 170–340 万 token）。
- 评测中发现并修复：`cordis.patch.yml` 把审批插件的 `exemptPrefixes` 设成了空表，只读的 `precheck_plan` 也弹确认（现为 `[precheck_]`）；DSH 结构化输出的 schema 只支持 type/oneOf/properties/required/additionalProperties/items/enum/const 和注解（`minItems` 会让子 Agent 启动失败，有测试检查）；数值核对把“第84、85、86条”的并列条号当数值（已按条号剔除）；`report_markdown` 末尾不要放给模型的说明（改为 `next_step`），报告越长模型越容易改字，审核意见不重复方案依据的条文原文。
- 模板里 0 是真值：要按“有没有”显示的段落用脚本给的布尔字段（如 `has_shortages`），不要用计数；同一个列表不能嵌套在自己的区块里当“非空”判断。
- P3 后回归（2026-11-03，v2 预设，deepseek-flash，各 3 次）：`s2-assess-1201`、`s4-escalate-1201`、`s4-warning-rejected` 3/3；`s4-plan-context-tamper` 首次在第二版上跑（`draft_sealing_plan`），3/3 未发布（1 次以向人提问结束）；`s4-explosion-risk` 2/3，失败的 1 次是贴报告时漏了升级建议里第39条原文一行（模型照抄遗漏，与 P3 改动无关）。每次 6.5–11.5 万 token、41–75 秒。
- P4（2026-11-04）：技能 `effect-review` 与固定流程 `review_effect`（since 缺省时经 actions 的只读工具 `query_last_issued` 取最近一次下发时间；采集清单的 `before_since_h` 按 since 取窗口），与第一版 5 个时刻逐测点一致；`check_mine(watch)` 登记 DSH 后台任务（核心 `src/evidence/watch.ts`），发现异常、到期、取数连续失败时结束并唤醒，唤醒失败按退避补发；`check.py` 加“数据中断”（按 `data_gap_h` 时长，不按检测周期：束管、安全监控的采样周期都长于 10 分钟的检测间隔）；持续检测期望测试（`test_watch_expectations.py`，s1/s3/s6）；评测运行器支持 `advance_h`、`answers`，新用例 `s4-watch-wake-assess`。停用第一版：`make up` 只启动 mine_data、actions，bundle 里只有第二版主预设（`profiles/v2.patch.yml` 删除，`dev.sh --v2` 保留为空操作），第一版技能草稿删除，`eval/baseline.py` 改跑技能脚本（结果与第一版相同）；第一版 Python 包暂留作对照。复盘时间线 `review_timeline` 尚未迁移。
  评测（只有 mine_data、actions + bundle 主预设，deepseek-flash，各 3 次，2026-11-04）：全部通过 20/24。`s4-escalate-1201`、`s4-warning-rejected`、`s4-plan-context-tamper`、`s4-watch-wake-assess` 3/3；`s2-assess-1201`、`s2-effect-after-inerting`、`s4-explosion-risk`、`s4-sealing-plan` 2/3，失败的 4 次都只是 `report_pasted`：模型贴长报告（19–72 行）时改写或漏了 1–3 行。每次 5.5–16 万 token。
- 报告交付（2026-11-04）：固定流程（run_fire_assessment、review_effect、draft_sealing_plan）的完整报告作为文件交付（present，网页端在回答下方显示交付卡片，点开在右侧预览，按 Markdown 渲染），模型只拿到并原样贴出代码按技能模板（`templates/brief.md`）生成的要点 `brief_markdown`（几行，含风险图）；`grade.py` 的 `report_pasted` 有要点时查要点，忽略加粗标记。要点里避免嵌套括号、带逗号的标签和附加说明句，这些最容易被模型改写。评测（8 个用例各 3 次，无接口错误）：全部通过 23/24，失败的 1 次是模型改写了要点里“研判说明”那一句（阶段模型写的文字，数值仍有依据）。
- 待办（第二版）：`review_timeline` 迁移；唤醒失败记进记录；第一版 Python 包和 `docs/workflow-design.md` 的清理。
- P5（2026-11-04）：模拟矿井演示模式（`RESCUE_SIM_MODE=demo`：常态连续运行、`/sim/demo/inject` 事故注入（过程参数从注入时刻换、剧本平移、物理状态连续）、`/sim/overrides` 读数设置（只作用于设置之后、历史不变）、只读看记录目录的异常检测记录自动暂停）与演示控制台 `/console`（单页，页面公开、接口带控制令牌）；`scripts/sim.py` 加 normal、inject、continue、override。Docker 部署 `docker/`（Python 服务镜像、DSH 镜像（构建时跑技能自检）、compose：只映射 13080、13090，令牌按服务分开、DSH 不拿控制令牌，技能目录只读挂载）、`scripts/demo.sh`（`make demo-up/down/reset/logs`）。`make demo-up` 从零启动三个容器健康，网页端一次性检测、控制台在容器里可用。
- 容器部署要点：MCP SDK 的 DNS 重绑定防护缺省只认本机 Host，容器间按服务名访问会 421，用 `RESCUE_MCP_ALLOWED_HOSTS` 加白名单（`rescue_contract.serving.transport_security`）；证据插件的脚本运行目录缺省在记录目录旁，容器里单独给 `RESCUE_RUN_DIR`；DSH 不接受通配地址，入口脚本绑定容器地址并用 `--public-url`、`--trusted-host` 指向宿主机端口；新的 DSH_HOME 会弹预览声明、没有缺省模型，入口脚本第一次建配置档时写用户层 patch。容器里取数失败时模型会申请提权自己建目录（违反 persona），演示中出现提权确认一律拒绝。

## 第三版进度（`v3` 分支）

- P0（2026-11-05）：V1、V2 通过（验证清单 #24，验证代码在 `spike/v3-wake` 分支，不合入）：插件扫描事件目录，用 `ctx.sessionController.create/resolveAgent` + `agent.followup` 在没有任何网页端连接时唤醒值班会话，冷会话、热会话、从零新建三条路径都成立，退路不启用。注意：消息来源 `kind` 不能是 `plugin`（用 `rescue-sentinel`）；投递是至少一次，事件要带编号让智能体去重；值班会话要用 `workspaceId` 放进默认工作区并命名（未验证）。下一步 P1（rescue-mine 接口）。
