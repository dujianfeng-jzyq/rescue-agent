# 架构说明

本文说明现行结构（第二至第五版累计）：各部分怎么连接、为什么这样分。设计取舍见 `docs/decisions/`（ADR 0007、0008、0009），各版本的完整设计见 `docs/design-v2.md`、`design-v3.md`、`design-v4-*.md`、`design-v5-demo-flow.md`，各阶段的实施记录见 `docs/progress-log.md`。

## 1. 分层

| 层 | 形式 | 放什么 | 不放什么 |
| --- | --- | --- | --- |
| 专家经验 | 技能（`skills/<技能名>/`） | 流程（SKILL.md）、算法（`scripts/`）、报告模板（`templates/`）、参数和依据（`references/`）、自检样例 | 数据访问、凭据、对话 |
| 智能体 | DSH 预设 `rescue-commander`（`dsh-bundle/presets/`） | 人设、挂载的工具和插件 | 业务计算 |
| 证据与固定流程 | DSH 插件（`dsh-bundle/src/`） | 采集证据、运行技能脚本、记录、风险检测（一次性、持续）；典型场景的固定步骤；知识库检索和查条 | 领域判据（在技能里） |
| 横切控制 | DSH 插件（`dsh-bundle/src/approval`、`audit`） | 红线预检 + 人工确认、审计 | 业务逻辑 |
| 外部服务 | MCP（Python，`mcp/`）和常驻进程 | 矿井数据（模拟矿井 / 真实矿井适配器）、指令出口 actions（guard）、常驻监听器 sentinel | 对话、提示词 |
| 契约 | Python 包 `rescue_contract` | 数据模型（含记录）、红线引擎、方案校验、记录核对 | 任何具体矿井的数据 |

原则：**会算错的东西放在技能脚本里并有自检样例，会说错的东西交给固定流程和模板，会做错的东西放在插件和 actions 里拦住。**
判断做成插件还是 MCP：只在 DSH 里用、要读写会话工作区、要设置页、要调用其他工具或起子 Agent、要交付文件或挂在工具管道上的，做成插件；DSH 之外也要用、在外部平台、要与 DSH 进程分开持有状态的，做成 MCP（`docs/design-v2.md` 4.5）。

## 2. 技能的两种用法

同一个技能目录有两个使用方：

| | 模型（DSH 技能） | 证据插件（`runScript`） |
| --- | --- | --- |
| 读什么 | SKILL.md | `scripts/`、`templates/`、`references/` |
| 在哪里运行 | bash 沙箱（workspace-write，只能写会话工作区） | DSH 进程起的子进程（不在沙箱里，环境变量按白名单传，设超时） |
| 结果 | 参考，不能被指令引用 | 记录（带脚本和输入的 sha256），可以被报告和指令引用 |

固定流程里需要模型的阶段，阶段指令也取相应技能的 SKILL.md。所以专家改一处，三处都用上。脚本是受信代码：子进程能写 DSH 用户能写的任何地方（验证清单 #22），只经评审和 CI 进入技能库，部署时技能目录只读挂载。

## 3. 主预设与工具

只有一个预设 `rescue-commander`：

| 能力 | 来源 |
| --- | --- |
| 技能、bash、文件、ask_user、todo、present | DSH 自带插件 |
| 后台任务（`job_output`、`job_list`、`job_kill`） | DSH 自带 `dsh-jobs-local`、`dsh-tool-jobs` |
| `check_mine`、`get_record`、`export_record` | 证据插件 `rescue-evidence` |
| `run_fire_assessment`、`draft_sealing_plan`、`draft_firefighting_plan`、`review_effect`、`assess_unsealing`、`review_timeline` | 固定流程插件（`dsh-bundle/src/fire-loop/`） |
| `get_response_options`、`get_field_conditions` | 固定流程插件：处置选项卡片、封闭方案现场条件卡片的内容由代码给出（智能体用 `ask_user` 原样问，不自己编） |
| `get_incident`（含各告警是否已处理的 `alert_status`）、`open_incident`、`set_incident_status`、`add_followup`、`complete_followup`、`close_incident` | 事件插件（`dsh-bundle/src/incident/`） |
| `map_view` | 地图插件（`dsh-bundle/src/map/`，只有放了地图包才有界面） |
| `kb_search`、`kb_get_articles`、`kb_list_datasets` | 知识库插件 `rescue-knowledge` |
| 加密监测（`start_monitoring`）、发布预警、升级、撤人通知、下发封闭方案（`issue_sealing_plan`）、下发防灭火方案（`issue_firefighting_plan`）、启封审批（`approve_unsealing`）、调度单；`precheck_plan`、`query_last_issued` | actions MCP（guard，唯一挂 actions 的预设） |

不挂：mine_data（读数经证据插件，留有记录）、delegate、联网、知识库写入、DSH 自带的 workflow 工具（模型现写脚本）。

固定流程的步骤由代码决定：采集证据 → 运行技能脚本 → 渲染报告 →（需要时）模型阶段 → 交付。模型阶段由插件用 `ctx.workflowEngine.start` 运行插件里固定的脚本，只能在给定清单里选择，说明文字里的数值必须能在本次记录里找到；用 `tools.guard` 限制阶段子 Agent 只能提交结构化结果。缺人工输入时：封闭方案的 5 项现场条件由代码给出的卡片（`get_field_conditions`）问操作员，答案经 `draft_sealing_plan` 的 `field_choices` 交回、由代码换成数值（换不了就报错重问）；防灭火方案的可选步骤由橙色处置选项卡片的答案（`selected_options`）强制决定，模型不能增减。红线预检不通过又缺输入时，流程返回结构化问题（`HumanInputRequest`）。

**交付格式**：固定流程把完整报告和方案作为文件交付，格式是 Word（`.docx`，`src/evidence/docx.ts` 把技能模板渲染出的 Markdown 转成 Word）；记录里的 content 和给模型的要点（`brief_markdown`）仍是 Markdown。风险图是 SVG，Word 里不嵌入，只写一行说明，图文件仍单独交付。

## 4. 风险检测、常驻监听与事件

- **常驻监听器**（`mcp/sentinel`，不经模型）：按矿井时钟每 10 矿井分钟取 `check` 证据、跑 `fire-assessment` 的 `check.py`，基线 = 上一次已上报的状态；升级类立即告警，数据类异常连续多次才告警，恢复后基线下调；接入矿井自带报警；跟踪计划到点、批准超时也由它写告警。告警写进**事件库**（`var/incidents/<事件编号>.jsonl`，只追加，折叠出状态；Python 与 TS 共用折叠样例）。事件库不是记录库：检测摘要不能被指令引用。
- **唤醒**：DSH 里的唤醒插件扫描事件库，把同一事件的待投递告警合成一条消息投递到值班会话（没有网页端连接也行），写 `delivered`，失败按退避重试。消息由代码生成：已处理的告警标注“重复投递”并换掉“立即研判”的要求（“是否已处理”由 `alertStatuses` 按时间线判断，不靠智能体读）。智能体被唤醒后自己研判、选处置、登记跟踪计划（`add_followup`，到点由监听器唤醒复评）。
- **一次性检测**：`check_mine(mode=once)`：采集 → `check.py` → 检测记录 → 结果卡片。
- **会话内持续检测**（可选）：`check_mine(mode=watch)` 用 `ctx.jobs.start` 登记后台任务，不经模型循环检测，发现异常时结束并唤醒智能体；日常由常驻监听器负责，只有操作员明确要求本会话自己盯时才用。唤醒失败的补救见验证清单 #23。

采什么、怎么判、什么算异常都在技能里（`references/collect.yaml`、`scripts/check.py`、`references/watch.yaml`），插件和监听器不含领域判据。

## 5. 一次指令的路径

```
主预设调用 mcp__actions__issue_sealing_plan(plan)      plan.record_id 指向 draft_sealing_plan 生成的方案记录
  │
  ├─ DSH tools/pre-execute ── rescue-approval
  │     1. POST actions:/redlines/check {tool, arguments}
  │        └ rescue_contract.validation.precheck：方案类型 + 记录时效 + 记录核对（重算 content_sha256）+ 红线引擎
  │        不通过 → deny(RESCUE_RED_LINE)；不可达/超时 → deny(RESCUE_PRECHECK_UNAVAILABLE)
  │     2. 通过 → ask：确认弹窗显示 ApprovalSummary（要做什么、引用的记录、核对结果、人工确认项）
  │
  ├─ 人工确认（动作分级表的 C 级）；批准超时（真实时间）后按表的 on_timeout：缺省只升级提醒、不执行，只有加密监测自动放行
  │
  ├─ actions 服务端：再校验一次（不信任客户端），同样按记录核对
  │     不通过 → ToolError("指令未发布。…")
  │     通过 → 经 rescue-mine（send_notice / dispatch_order / submit_treatment）交给矿井执行，写指令台账（含 Origin、矿井回执、事件编号）
  │
  └─ DSH tools/result ── rescue-audit 写审计日志（issued / denied / failed）
```

**记录核对**：方案的现场条件和必备步骤、预警等级、升级建议都按引用的记录核对，对不上就拒绝；预警等级与记录不同时必须写 `override_reason` 并在弹窗显示。记录只有 DSH 进程能写（证据插件），bash 沙箱写不了（只读），actions 只读挂载。固定流程在编写方案时就调 `precheck_plan`，所以正常流程里第 1 道很少触发；它是兜底。

## 6. 知识库

`rescue-knowledge` 插件：

- `kb_search` 经 RAGFlow 语义检索（法规、预案、案例），结果能对应到条文索引的补上条号；RAGFlow 不可用时降级到本地条文关键词检索，结果里标明 `source`。
- `kb_get_articles` 按文档和条号批量从**条文索引**取完整条文，不经 RAGFlow、不由模型拼接。条文索引在入库时按“章—节—条—款—项”切分生成（离线脚本，专家抽查），随部署发布。
- 红线规则所依据的条号由 Python 规则表导出（`make schema`），插件不在 TS 里再写一遍规则。
- 维护工具（上传、解析、写切片、建索引）本次不开放给模型。

## 7. 模拟矿井与演示控制台

`mine-data-sim` 是集总参数的采空区自燃模型：高温点温度随氧化放热、散热、注惰/灌浆等处置变化；标志气体由温度和供氧推出，再按风流稀释到各测点；叠加确定性噪声和可编排的传感器故障。

- 智能体只能通过证据插件看到传感器读数，看不到真值（`/sim/state` 的 `truth` 只给控制台和评测）。
- 指令经 guard 确认后才作用于模拟器（guard 经 rescue-mine 的 `submit_treatment` 等写入工具，写入令牌只有 guard 持有）。
- 第二版增加（P5）：启动后常态连续运行；“开始演示”时把事故场景以当前矿井时间为起点注入；控制台可设置单个测点的读数（只改读数，不改物理模型）；倍速分段，检测到异常后自动暂停（模拟矿井只读挂载记录目录，只为演示，不是矿井接口的一部分）。这些只在 `/sim` 控制接口和控制台上，用单独的控制令牌，DSH 不持有。
- **演示导演**（第五版，`director.py` + `scenarios/demo/flow.yaml`）：控制台点一次“开始演示”，暂停即冻结时钟，导演逐步推进、读监听器的等级告警，到每一级暂停等操作员选处置，按矿井实际执行的处置（guard 经 rescue-mine 下发的）认出所选，按累计个数走分支剧本，走完自动结束。导演不判断预警等级；分支参数是演示剧本的占位值（ADR 0009）。
- 6 个标准场景同时是演示脚本、评测集、回归集（`scenarios/README.md`）；评测用手动时钟。

接入真实矿井：写一个实现相同 MCP 工具的适配器包（输入错误抛 `ToolError` 并写明原因；`get_personnel` 用 `PersonnelSnapshot.of(名单)` 构造），先通过 `python -m rescue_contract.conformance <URL>`，再替换 mine_data 的地址。

## 8. 第一版组件的去向（历史，2026-11-04 清理完成）

第一版的 Python 服务包（`ventnet`、`fire-model`、`dispatch`、`kb`、`workflow`）、研判快照和对第一版工具名的兼容都已删除；算法和参数迁到了技能脚本和 `references/`，工作流和快照由证据插件、固定流程插件和记录取代，`kb` 由知识库插件取代，`check_plan_redlines` 移到 guard 的 `precheck_plan`。需要查旧实现用提交历史；第一版的设计文档 `workflow-design.md` 已于 2026-11-10 删除。ADR 0006 仍保留，作为当时的决策记录。

## 9. 大模型

开发期用 DeepSeek API（DSH 自带提供方）。provider 的重试策略在默认可重试错误之外加上 `STREAM_CLOSED`（4 节）。本地化部署后用 `profiles/local-llm.patch.yml` 注册 OpenAI 兼容提供方 `rescue-local`。代码不依赖具体模型。

## 10. 运行时文件（`var/`，不入库）

| 文件 | 写入方 | 用途 |
| --- | --- | --- |
| `var/records/` | 证据插件（DSH 进程） | 证据、结果、报告记录（只新增不修改），指令发布时按编号核对 |
| `var/incidents/` | 监听器、事件插件、唤醒插件、审批插件、guard | 事件库（只追加）；`sentinel/` 下是监听器状态和心跳，`duty-session.json` 是值班会话编号 |
| `var/actions-ledger.jsonl` | guard | 已发布指令台账（含记录编号、Origin、矿井回执、事件编号） |
| `var/audit/tool-actions.jsonl` | rescue-audit | 所有指令类工具调用结果（含被拦截、被拒绝） |
| `var/map/` | 人工放置（地图包，git 忽略） | 通风图几何和映射表；真实矿井图不进仓库 |
| `var/log/*.log`、`var/run/*.pid` | 启动脚本 | 服务日志与进程 |
| `var/eval/` | 评测、验证 | 评测用的台账、记录、事件库、审计和验证日志 |
| `var/demo/` | Docker 演示环境 | 演示数据卷（记录、事件库、台账、审计、DSH 会话），不混进开发的 `var/`；`make demo-reset` 清空 |
