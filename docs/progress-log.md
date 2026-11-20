# 进度日志（第一至第五版，逐阶段记录）

> 本文件是原来 `CLAUDE.md` 里“第一版历史记录”“第二版进度”“第三版进度”三节的原文（整理时整体搬来，内容未改），再加上第四、第五版的进度。
> `CLAUDE.md` 只保留现状、约定和“已知坑”；要查某个阶段当时做了什么、评测结果是多少、踩过什么坑，看这里。
> 其中 2026-11-08 以前的评测数字、占位值、待办都是当时的状态，**现状以代码和各设计文档为准**（第一版的 Python 服务已删除，相关描述只作历史）。
> 阈值、参数、评测期望都是**开发占位值**，不要声称它们“正确”。

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

## 第二版进度（已合入 `main`）

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

## 第三版进度（2026-11-06 起合入 `main`；开发分支 `v3` 已删除，提交历史在 `main`）

- P0（2026-11-05）：V1、V2 通过（验证清单 #24，验证代码在 `spike/v3-wake` 分支，不合入）：插件扫描事件目录，用 `ctx.sessionController.create/resolveAgent` + `agent.followup` 在没有任何网页端连接时唤醒值班会话，冷会话、热会话、从零新建三条路径都成立，退路不启用。注意：消息来源 `kind` 不能是 `plugin`（用 `rescue-sentinel`）；投递是至少一次，事件要带编号让智能体去重；值班会话要用 `workspaceId` 放进默认工作区并命名（未验证）。下一步 P1（rescue-mine 接口）。
- P1（2026-11-05）：rescue-mine 接口。契约 0.10.0：`SensorPoint` 加 `panel`、`role`（`SensorRole` 占位词表），新增 `MineCapabilities`、`MineDescription`、`MineAlarm`、`EquipmentStatus`、`NoticeRequest`/`DispatchRequest`/`TreatmentRequest`、`MineReceipt`；接口表 `rescue_contract/interface.py`（接口版本 1.0.0，与数据契约版本分开）。mine-sim 新增 8 个工具：`get_capabilities`、`describe_mine`、`get_alarms`、`get_equipment_status`、`send_notice`、`dispatch_order`、`submit_treatment`、`get_action_status`（幂等 key；调度模拟异步回执，执行中 30 个矿井分钟后完成，占位值）。写入类工具认写入令牌（中间件按 JSON-RPC 的工具名区分，批量请求里夹带写入调用同样被拒；写入令牌也能读）。conformance 按接口表检查（工具集合与 `get_capabilities` 声明一致、拓扑与角色一致、报警设备回执），写入类检查默认关闭。采集清单改按角色取数，Python 和 TS 两端同一规则（`ids` 被拒绝，`panel: true` 按测点的 `panel` 字段匹配），技能自检校验角色在词表里。`docs/rescue-mine-interface.md` 由代码生成。验收：`make check` 通过（Python 330、TS 117），基线评测 6/6 且各等级首次到达小时数与改动前逐项相同，技能自检 56/56。服务名仍是 `mine_data`（端口 8101、`RESCUE_MCP_MINE_DATA_URL` 不变），没有改名以免牵动部署；actions 在 P1 时仍经 `/sim/interventions` 转发处置，P2 已改为 guard 调 `submit_treatment`。
- P2（2026-11-05）：守卫拆分（ADR 0008）。guard（`mcp/actions`）校验通过后调用 rescue-mine 的 `send_notice`（预警、升级、撤人通知）、`dispatch_order`（调度单）、`submit_treatment`（方案按步骤逐条、撤人涉及的巷道）；不再调 `/sim/interventions`，不再持有模拟器控制令牌（docker 里 actions 改持 `RESCUE_MCP_MINE_ACT_TOKEN`，`scripts/demo.sh` 给旧的 `var/demo/tokens.env` 补这一行）。台账每条带 `record_ids`、`origin`（取自记录）、`mine_receipts`、`incident_id`（P3 起填）；矿井中途失败记 `failed` 和已执行的回执，失败的不算已下发；幂等键由指令内容生成，重复提交矿井只执行一次；矿井拒绝执行的步骤（如监测）列在 `mine_not_executed`。撤人名单、调度单在预检阶段按矿井的人员定位、网络、物资台账核对（调度不超库存）。契约 0.11.0：删除 `Snapshot`、`SnapshotKind`、`HumanInput`、各指令的 `snapshot_id`；`WarningNotice`、`Escalation` 的 `record_id` 必填；`TreatmentRequest.panel` 可选。**删除 `issue_firefighting_plan`、`approve_unsealing`**（只能按第一版草案快照核对，没有第二版流程）：启封审批和防灭火方案下发要随新流程按记录重做（演示故事线第 7 步需要）。`issue_sealing_plan` 只收 `record_id`；`precheck_plan` 去掉 `redlines_only`（现在只查类型和红线）。动作分级表 `action_policy.py` 含确认人、批准超时、超时升级对象（占位值，P3 的监听器执行超时升级）；审批插件按它放行，不再按名字前缀；审计插件记录 guard 的全部调用。评测 `snapshot_referenced` 改名 `record_referenced`。验收：`make check` 通过（Python 335、TS 120），基线评测 6/6 且数字与改动前相同，技能自检 56/56，三道防线集成测试（审批插件的真实 TS 代码对真实 guard 预检，再绕过审批直接调 guard）8 个通过；端到端（`make dev-eval`，deepseek-flash，各 1 次）`s4-sealing-plan`、`s4-warning-rejected` 全部通过，台账里有矿井回执（约 9 万 token、51–57 秒）。**未做**：批准人按角色限定（需要 DSH 的用户身份）；超时升级（P3）；复盘时间线把台账回执和 rescue-mine 的后续状态对上（P5）；`grade.py` 的第一版兼容（P5）。
- P3（2026-11-06）：监听与事件。**事件库**（`rescue_contract/incidents.py`，`var/incidents/`）：一个事件一个 jsonl，只追加，条目 kind 见模块说明；状态（监测中/处置中/已稳定/已关闭）、待确认告警（没有 delivered/acked 的 alert）、跟踪计划都是折叠出来的，Python 与 TS 共用样例核对折叠结果。**监听器**（`mcp/sentinel`）：按矿井时钟每 `interval_min` 取 check 证据、跑 `check.py`（新增输出 `escalated`：等级升高、阶段推进、新实时报警），基线 = 上一次已上报的状态；升级类立即告警，数据类异常（测点失效、数据中断）连续 `confirm_checks` 次才告警；告警后基线更新，同一异常不重复；不比基线更糟连续 `recover_checks` 次后基线下调，回到正常写“恢复”、事件转已稳定；取数连续失败 `degrade_failures` 次写“监听降级”；接入矿井自带报警（`get_alarms`）；跟踪计划到点写 followup_due 并告警；状态落盘，重启不重复告警；心跳文件供控制台显示“监听中断”。**事件插件**：get_incident、open_incident、set_incident_status、add_followup（只收“多少矿井小时后”，到点时间由代码算）、complete_followup、close_incident。**唤醒插件**（验证清单 #24 的做法正式化）：扫描事件库，同一事件的待投递告警合成一条消息投递到值班会话（`duty-session.json` 记会话编号），成功写 delivered，失败写 delivery_failed、按退避（占位 30/120/600 秒）重试，用完放弃（告警保持“未确认”，控制台显示）；`createUserMessage` 收进 `src/adapter/dsh.ts`。**控制台**：`/sim/state` 带 `incident_store`（事件、未确认告警、跟踪计划、时间线、监听器心跳），页面新增“值班事件”区块；新告警同样触发演示的自动暂停。验收：`make check` 通过；监听器期望测试（`sentinel/tests`：s1 30 小时无告警、s3、s6 在期望时刻（数据类异常最多晚 confirm_checks−1 个间隔）告警；去重、升级、好转后再升、恢复、毛刺确认、降级与恢复、重启不重复、跟踪计划到点、矿井报警接入）；TS 事件库/唤醒/工具测试；真实 DSH 上（deepseek-flash，一次）：注入事故后监听器告警，唤醒插件在无网页端连接时投递，值班会话里智能体自己调 get_incident、run_fire_assessment 并完成（token 未统计）。**未做**：C 级动作超时升级（需要 guard 记待批准项，P4）；`workspaceId` 与值班会话标题（插件有 `workspaceId` 配置，未验证）；值班会话长时间空闲、操作员同时输入时又来事件；Docker 演示环境的 sentinel 容器（compose 已加，未实测）；智能体把研判/对外动作自动记进事件时间线（P4 随工具重组）；`review_timeline` 读事件库（P5）。已观察（原因未查）：倍速 3600 的那次，监听器在矿井时间 03:23 告警“阶段推进为缓慢氧化”，自动暂停有轮询延迟，约 40 矿井分钟后智能体研判时阶段已是“未见自燃征兆”。阶段升高目前立即告警、不等确认；是否也要连续确认，待查原因后定。
- P4（2026-11-06）：智能体改造的第一部分。**人设**改为矿山应急救援专家（`commander.patch.yml`）：自己研判、自己选处置；**只有三种情况可以向人提问**（对外动作要批准、缺信息、事实冲突）；检测到异常后直接研判，持续检测任务结束时不再问“要不要研判”；数据不可信时如实说明缺什么、不支撑对外动作。**流程型技能** `skills/fire-incident-management`（SKILL.md + `references/followup.yaml`，`skill.yaml` 写 `kind: flow`、没有脚本，自检放行；正文不含数值）。**闭环跟踪**：`add_followup` 的 `after_h` 可省，缺省和上限取自 `followup.yaml`（占位 2 / 48 矿井小时）。**事件时间线自动记录**：固定流程（run_fire_assessment、review_effect、draft_sealing_plan，`fire-loop` 配置 `incidentDir`）把结果写成 note 记进该工作面未关闭事件；guard 把每次对外动作的结果（通过、拒绝、失败）记进时间线（by=guard），台账 `incident_id` 取该工作面未关闭的火情事件（没有为空）；这两处都是代码写，不靠模型。**决策评测**（`eval/agent/`）：运行器支持用例的 `incident:`（在事件库建事件和告警并标为已投递，提问里的 `{incident_id}`、`{alert_id}`、`{mine_now}` 由它填；提问压成一行发送）；grade 新增 `no_questions`、`tools_not_called`、`incident_read`、`followup_added`、`final_mentions`；新用例 `v3-wake-anomaly`、`v3-followup-due`、`v3-degraded`；`s4-watch-wake-assess` 去掉“被问后回答”改查 `no_questions`、`no_issue`。评测运行器修了一个 bug：第二个人工确认的按钮名要精确匹配，否则点到“Error: the user rejected tool …”那一行而挂到超时。评测（deepseek-flash，各 1 次，2026-11-06）：三个 v3 用例、`s4-watch-wake-assess`、`s4-warning-rejected`、`s4-sealing-plan`（按事件工具不算“轮询”的新规则重判）、`s2-assess-1201`、`s2-effect-after-inerting`、`s4-explosion-risk`、`s4-plan-context-tamper` 全部通过；`s4-escalate-1201` 只挂在 `no_wait_polling`（等阶段子 Agent 时 read/bash/kb 3 轮，单次样本，约 9.8 万 token）。智能体现在会主动提交预警和升级（评测里被拒），所以研判类用例的 `no_actions` 在值守用例里换成 `no_issue`。**未做、留待后续**：工具重组（design-v3 5.2 想把固定流程拆成 assess_fire / assess_explosion 等，现有 run_fire_assessment 已含爆炸风险和升级建议，拆分收益小、要重做测试，暂缓）；参数按专业知识重新设计（没有领域依据，不动占位值）；C 级动作超时升级（需要 guard 记待批准项和角色/时限）；`review_timeline` 读事件库（P5）；各用例只跑了 1 次，稳定性要多次重复。
- P5（2026-11-06）：**复盘**：技能 `incident-review`（`timeline.py` 把事件库条目整理成时间线，算告警/研判/复评/封闭方案次数、对外动作（已下发/被拒绝/执行失败）次数、跟踪计划次数、发现异常到研判/到下发的分钟数；模板 report、brief；自检样例 7 个技能 63 个），固定流程 `review_timeline`（不给 incident_id 取最近更新的事件；复盘报告作为文件交付，模型原样贴要点）；评测用例 `v3-review-timeline`（尚未跑）。**值班会话**（改自 P3 的未验证项）：唤醒插件在值班目录上经 `workspaceController.create({path})` 建（或复用）工作区“值班”，会话用 `workspaceId` 创建，侧栏不再进折叠的 Ungrouped；创建后立刻 `sessionController.rename` 设标题“值班会话（监听器告警）”——显式标题会固定住，之后不再自动生成（日志里 `session/title` 来源为 `user`；此前自动生成的标题日志里查不到，操作员也没法按编号找）。两处失败都只记警告，不影响告警投递。**故事线脚本** `eval/agent/story.py`：扮演操作员，控制模拟矿井（常态 → 注入 s4-escalation → 推进时钟），在网页端值班会话里说话、按策略点确认（放行预警、升级、封闭方案下发，其余拒绝），判定看事件库时间线。步骤：1 常态 2 注入事故，监听器开事件 3 唤醒值班会话研判、对外动作弹确认 4 操作员决定封闭，方案经合规审核、确认后下发 5 推进到跟踪计划到点，监听器唤醒复评 7 复盘。**2026-11-06 整条走通**（deepseek-flash，1 次，倍速 600，约 7 分钟）：监听器告警、已投递、研判记入时间线、对外动作记入时间线、至少一次下发成功、登记跟踪计划、跟踪计划到点、效果复评记入时间线、复盘调用 review_timeline 共 9 项全部通过；事件时间线 19 条。故事线没有“启封审批、事件关闭后的封闭流程”（guard 的 approve_unsealing、issue_firefighting_plan 在 P2 已删除，要随新流程重做）和设计里的“恶化分支单独演示”（s4 场景本身就走到红色，升级在步骤 3 由智能体提交）。**未做**：C 级动作超时升级；复盘把台账回执和 rescue-mine 的后续状态对上；Docker 演示环境里的 sentinel 容器和值班工作区（compose 已改，未实测）；决策评测各用例只跑了 1 次，稳定性需要多次重复；`make story` 只跑了 1 次。
- 遗留项收尾（2026-11-06）：**批准超时升级**（design-v3 6.3）：审批插件监听 `approval/request`，弹确认前写 `approval_pending`、有结果后写 `approval_decided`（事件时间线里的 note，`approval_key` 配对，带确认人、批准超时、升级对象，取自动作分级表）；预检摘要 `ApprovalSummary` 新增 `incident_id`（契约 0.12.0，guard 按引用记录的工作面查）；监听器扫描未决确认，超过 `approval_timeout_min`（真实时间）写 `approval_escalated` 和 `approval_timeout` 告警，只升一次；告警唤醒值班会话（提示智能体告知操作员、不要重复提交）；控制台事件卡片显示“等待人工确认/已超时，已提醒谁”。事件折叠新增 `pending_approvals`（Python/TS 共用样例）。实测（临时把 publish_warning 的超时改成 1 分钟）：待确认出现后 60 秒监听器写升级记录和告警，告警投递到值班会话。**复盘对回执**：guard 记进时间线的对外动作带 `mine_receipt_ids`，`review_timeline` 复盘时逐个向矿井查 `get_action_status`，脚本算已完成/执行中/失败或被拒/查不到的回执数；查不到的记原因。时间线里还列人工确认的等待、结果和超时升级。**启封审批按记录重做**：技能 `unsealing-plan`（`unsealing_plan.py`：封闭时长、氧浓度、CO 及上升速率、乙烯、采空区温度逐项判定，全部满足才生成固定步骤的启封方案；判据和步骤在 `references/readiness.yaml`，占位值，待专家审定；总判定数、满足数、不满足数由脚本给出）、固定流程 `assess_unsealing`、guard 的 `approve_unsealing(record_id)`（只收启封评估记录，核对结论为“具备启封条件”、时效和红线 RL-164，再按步骤交给矿井；C 级，确认人总指挥，批准超时 30 分钟升级给矿长，均占位）。没有模型编写和合规审核阶段（方案由代码生成，红线在预检和 guard 强制）。**Docker 演示环境实测**（`make demo-up`，4 个容器含监听器的健康检查）：整条故事线 9 项通过，事件时间线 43 条；Docker 网页端是中文界面，评测运行器同时认中英文确认按钮，判定脚本认 `DSH_HOME`（会话日志在 `var/demo/dsh-home`）。故事线脚本补了：智能体提问现场条件时在提问卡片里作答（缺信息，允许提问）、点会话行而不是文字、不等隐藏的输入框。**决策评测多次重复**（deepseek-flash）：`v3-followup-due` 3/3、`v3-review-timeline` 3/3、`v3-unseal-ready` 3/3、`v3-unseal-too-early` 3/3、`v3-degraded` 3/3（先 0/3，原因见下）、`v3-wake-anomaly` 2/3（1 次是智能体改写了要点，`report_pasted`）。修了的问题：`record_referenced` 判定漏认 `assess_unsealing`；启封要点里“6 项”是智能体自己把 5+1 加出来的（数值溯源 1/3 失败），改为脚本给判定总数；`v3-degraded` 里智能体向人确认“链路是否恢复”属于规则允许的“缺信息”提问，原来要求 0 次提问过严，**放宽为最多 1 次，关键词也放宽**（如实记录：这是调整期望，不是智能体变好了）。旧用例回归（各 1 次）：`s2-assess-1201`、`s2-effect-after-inerting`、`s4-escalate-1201`、`s4-explosion-risk`、`s4-sealing-plan`、`s4-warning-rejected`、`s4-watch-wake-assess` 通过；`s4-plan-context-tamper` 只挂在 `no_wait_polling`（`no_issue` 通过）。**仍未做**：批准人按角色限定（需要 DSH 用户身份）；启封后按复燃征兆自动提示重新封闭的流程（只有技能文字说明）；启封评估的判据/步骤由专家审定；故事线只在本机和 Docker 各跑通了几次，没有统计稳定性。
- 代码审查收尾（2026-11-06，合入 `main` 前，`/code-review high`）：**启封按序下发**：guard 的 `approve_unsealing` 用有序下发，前置步骤（检查、断电、撤人）矿井返回 rejected/failed 就停止、台账记 `failed`，不再继续提交 `unseal`；模拟矿井现在能执行 `monitor`、`power_cutoff`（只记进 action_log，不改物理模型），封闭方案的监测步骤也因此由 rejected 变 completed。**启封判定 fail closed**：任一测点无可用读数或最新读数过旧（`readiness.yaml` 的 `max_stale_h`，占位 2 小时，待专家审定）则该项“无法判定”，不再拿剩下的测点判满足。**哨兵**：取不到矿井时钟时仍先处理批准超时升级。**其他**：`unsealing-plan` 的三份共用辅助模块登记进一致性测试；复盘显示“批准超时”告警；启封弹窗标题为“批准启封”；复盘并行（上限 6）查回执；审批插件 `asked` 表加过期清理（`askedTtlMs`，缺省 1 小时）。验收：`make check` 通过（Python 360、TS 150，技能样例 77），`v3-unseal-ready` 在真实 DSH 上（deepseek-flash，1 次）全部通过、5 个矿井回执全部 completed。重新生成技能样例（`make_skill_cases.py` 全量）会改写约 26 个无关的 `*.input.json`，提交前要回退。
- 主线故事线重写（2026-11-07）：`eval/agent/story.py` 改用场景 s3 按预警等级分段推进（演示模式、注入后暂停时钟，每段推进到该等级、等监听器告警和值班会话处理完再进下一段）：黄色（研判、告知、登记跟踪，无对外动作）→ 橙色（防灭火方案、预警，确认下发）→ 红色（预警、升级、撤人、封闭方案）→ 封闭后复评 → 推进到封闭后约 320 矿井小时评估启封（不具备则每轮再推 48 小时，最多 4 轮，期间不得下发）→ 启封方案确认下发 → 复盘。检查点按分级决策表（占位）写；操作员代答提问卡片（封闭决定、现场条件、复评起点）。**橙色处置后 s3 是否自己升红取决于智能体选的措施**（只注氮约 70 小时升红；注氮加注浆受控），受控时 48 矿井小时后注入系数 3 的漏风突增；系数 8 会让封闭后的火区停在 115 ℃、几百小时都降不到启封温度，不要用。哨兵修了一个问题：矿井时钟倒退（模拟矿井重置）时清掉旧基线和已见报警，否则上一次演示留下的红色基线会压住新历史里的黄色（`test_clock_going_back_resets_baseline`）。一次完整走通（deepseek-flash，本机，约 12 分钟）13 项检查全过；红色阶段智能体是否自己起草封闭方案每次不同（观察项，不计入通过）。**故事线复燃分支不在其中**。
- 收尾（2026-11-07）：**防灭火处置方案**（技能 `firefighting-plan`、固定流程 `draft_firefighting_plan`、guard `issue_firefighting_plan(record_id)`；封闭方案流程抽成带配置的通用函数；合规审核按方案类型分审核项；分级决策表在 `skills/fire-incident-management/SKILL.md`，均为占位）已重新有了第二版流程，上面 P2 条目里“删除 issue_firefighting_plan”指的是旧的第一版草案。**报告交付改为 Word（.docx）**：固定流程交付给用户的报告和方案（研判、封闭方案、防灭火方案、复评、启封评估、复盘）由 `dsh-bundle/src/evidence/docx.ts` 把模板渲染出的 Markdown 转成 .docx（依赖 `docx` 包）；记录里的 content 仍是 Markdown（事实来源，brief 也是 Markdown 给模型），风险图是 SVG，Word 不嵌入，只写一行说明，图文件仍单独交付。**故事线加了步骤 8 收尾**（智能体完成跟踪计划、确认无未确认告警后关闭事件）。**`grade.py` 的第一版兼容已清理**（删除 workflow/fire_model/dispatch/ventnet 的工具名表和 V2_EQUIVALENT 映射，`V2_FLOW_TOOLS` 改名 `FLOW_TOOLS`；用例的 `tools_called` 直接写第二版工具名；`mcp/tests/test_agent_grade.py` 同步改写）；清理前后对 40 个已有会话逐项判定结果完全相同，5 个旧用例真实运行通过。**技能样例已用 `make_skill_cases.py` 全量重新生成**（旧样例缺 panel/role 字段、防灭火方案的依据条文也没跟上 `plan.yaml`），自检 94/94、测试通过。
- Word 预览（2026-11-08）：Docker 里网页端预览 `.docx` 显示“Office previews are unavailable”，**原因是镜像没有字体**：DSH 的 Office 转 PDF（LibreOffice WASM）没有可用字体就拒绝转换（容器里直接调用 libreoffice-kit 得到 `No usable fonts were found`）。`docker/dsh.Dockerfile` 加装 `fonts-wqy-microhei`、`fonts-dejavu-core` 后预览正常（报告字体写的是微软雅黑，预览里会提示缺字体并用替代字体，Word 里正常）。本机有系统字体，一直正常。
- 稳定性与边界情况（2026-11-08）：故事线重复运行（deepseek-flash）：Docker 5 次完整走完（4 次 14/14，1 次 12/14——红色检查早于智能体完成、封闭太晚来不及等到启封条件），本机完整走完 4 次均 14/14；其余几次是评测脚本在回答提问卡片时超时崩溃（脚本问题，已加超时、未识别卡片跳过和重试）。智能体侧观察：黄、橙检查 10/10；红色阶段偶尔向操作员请示“是否升级”“是否加强注入”，红色是否自己起草封闭方案每次不同。值班会话边界情况（`eval/agent/edge.py`，Docker）：操作员插话同时来新告警、DSH 重启后又来告警，5 项全部通过。复燃分支未做。

## 第四版进度（2026-11-08 至 11-09，已合入 `main`）：通风图地图界面与撤人路线

设计：`docs/design-v4-map.md`、`docs/design-v4-evacuation.md`；验证记录：验证清单 #25–#36。

- **P0（2026-11-08）**：浏览器端插件、悬浮层（地图在下、DSH 三栏浮在上）、Node → 浏览器推送与鉴权、失败时退回 DSH 原界面（#25–#27）。真实矿井图不进仓库：`scripts/map/` 把 DWG 转 DXF、提取几何到 `var/map/geometry.json`（地图包，git 忽略，Docker 只读挂载 `/data/map`）。
- **P1 与大屏外壳（11-09）**：巷道填充（读 HATCH）、图例、标签防重叠、窄屏适配；自绘顶栏/左栏/右栏 + DSH 会话组件换皮，状态接口 `/rescue-map/state`（#28、#29）。
- **P2 实时叠加（11-09）**：映射表 `var/map/mine_map.yaml`；预警等级着色、矿井报警、火区、新告警自动定位（`src/map/autopilot.ts`）；模拟矿井的工作面名可换成与地图一致（`RESCUE_SIM_PANEL=211016`，Docker 用 `DEMO_PANEL`）（#30）。
- **P3/P4（11-09）**：右栏智能体状态读真实运行状态（`activity.ts`，只旁路观察）、点选引用；智能体的 `map_view` 工具（名称只能是地图上有的，标记的标签由代码从记录取，备注不能含数字，最多 12 个标记）与评测用例 v4-*（#31–#35）。人工确认走查：大屏皮肤下的安全链路原样保留（#32）。
- **撤人路线与受威胁人员（E1–E5，11-09）**：映射表扩展节点和通路；受威胁巷道着色、撤人路线（流动虚线）、人数徽标、“需人工研判”徽标、左栏“受威胁人员与撤离”，路线和人数由代码从 personnel 记录生成（#36）。
- **遗留**：过期（2 小时）变灰、隐藏（6 小时）只有单元测试，没在浏览器里看过；节点位置 N5→N6→N7 是粗略位置，等客户给现场图；状态落盘（DSH 重启不丢标记）、多网页端、操作手册（P5）没做。
- **踩到的坑**：没有模型密钥的 DSH 实例一直弹 “Get started” 对话框，遮罩挡住所有点击，测试前要按 Escape；验证界面时不要让本地 DSH 带着 `.env` 里的密钥跑，否则值班唤醒插件会真实调用模型。

## 第五版进度（2026-11-09 至 11-11，已合入 `main`）：按预警等级的演示流程与统一的人工确认

设计：`docs/design-v5-demo-flow.md`；决策：`docs/decisions/0009-demo-director-and-operator-cards.md`；验证记录：验证清单 #37–#39。

- **演示导演**（`mcp/mine-data-sim/src/rescue_mine_data_sim/director.py`，流程配置 `scenarios/demo/flow.yaml`）：控制台点一次“开始演示”，暂停即冻结时钟，导演先跳进再一步一步推，每步等监听器心跳追上再读事件库的等级告警；到每一级暂停，等操作员选处置，按所选**累计个数**走分支（goto 下一级 / end 跑一段后结束 / again 追加处置）。不判断等级，只读监听器告警。分支参数（如 `k_ox` 乘 0.64 使预警回落到黄色）是占位值，是演示剧本，不是模型预测，改 s3 场景参数后要重扫。
- **动作分级表 `on_timeout`**（`approve` / `reject` / `escalate`，缺省 `escalate`）：批准超时（真实时间）后由审批插件按表自动处理；**只有黄色的加密监测 `start_monitoring` 设为 `approve`**，其余对外动作（预警、升级、撤人、封闭、调度、防灭火方案、启封）都是 `escalate`（只升级提醒，不自动执行，有测试固定）。自动放行时要把 `req.signal` 换成“原信号 + 插件自己的信号”并在到点时中止，否则网页端确认卡片和会话上的 “Approval” 标记不会消失（验证清单 #37）。
- **加密监测**：guard 新工具 `start_monitoring(record_id)`，引用 `run_fire_assessment` 的预警等级记录，经 rescue-mine `submit_treatment`（monitor 步骤）执行。
- **处置选项卡片**：共用的处置选项表 `rescue_contract/response_options.py`（导出 `dsh-bundle/data/response-options.json`）；工具 `get_response_options`；橙色研判后智能体用 `ask_user` 多选卡片问操作员（注浆、堵漏；必做的注氮不放进卡片），`draft_firefighting_plan(selected_options=…)` 由代码强制方案的可选步骤正好是所选。多选卡片没选任何项时 Submit 是灰的，只能点 Skip，所以卡片写明“不追加就点跳过”。导演不再自己施加处置，而是看矿井处置日志里 guard 执行的处置（来源 `action`）认选项。
- **封闭方案现场条件卡片**：`rescue_contract/field_conditions.py`、工具 `get_field_conditions`、`draft_sealing_plan(field_choices, confirmed_by)`；5 项现场条件（只能由人确认）各带选项，也可输入实际数值，答案由代码换成数值，换不了就报错重问（验证清单 #39）。缺现场条件时封闭方案仍可下发（只在审批弹窗里提示），这是原有设计，没收紧。
- **重复告警**：`alertStatuses`（`dsh-bundle/src/incident/store.ts`）由代码按时间线判断告警是否已处理；唤醒消息对已处理的告警标注“重复投递”并换掉“立即研判”；`get_incident` 返回 `alert_status`；评测里唤醒用例的提示词用 `dsh-bundle/tools/wake-message.ts` 与生产同源生成。`v3-wake-repeat-dup` 2/6 → 6/6（原来的不稳定与提示词无关，回退提示词同样 2/6）。
- **评测与故事线**：用例 `operator_cards: true` 时运行器扮演操作员回答代码给出的卡片；`no_questions` 不计代码给出的卡片；`wake: true` 用例的提示词由生成器生成。全量 40 个用例（含探测用例 `exp-probe-env`，deepseek-flash，各 1 次）36 通过，其余 4 个里 3 个是用例没适配新流程（适配后 3/3、3/3、2/3），1 个是 `v3-wake-repeat-dup`（已修）；故事线 14/14（橙色卡片默认点跳过才会自然升红，追加注浆、堵漏则受控）；演示脚本 `eval/agent/demo_run.py`（点一次开始演示后扮演操作员，`STORY_ORANGE_OPTIONS` 控制橙色卡片选什么）。
- **遗留**：橙色累计 2 项（“效果有限，请追加”）只有单元测试，没在真实环境跑过；每个用例大多只跑 1 次，稳定性没统计；`s4-plan-context-tamper` 偶发 `no_wait_polling`；“演示开始”前不检查智能体和监听器是否在线；暂停时读数的轻微浮动没做。
- **踩到的坑**：`pkill -f <脚本名>` 会把执行它的 shell 一起杀掉，要按精确 PID 杀；`ruff --fix` 会删掉必要的 `# noqa`，要看 diff；评测运行器偶发“没有找到评测会话”（基础设施抖动，重跑即可）。
- **大屏布局调整（2026-11-10）**：聊天区从底部挪到右栏（智能体卡片在上、DSH 会话组件占满剩余高度），地图得到中间整块高度；隐藏演示用不上的 DSH 控件（视图标签、权限模式、模型选择、附件、用量统计、轮次导航），输入框不透明、不再盖住消息；监听器告警投递到值班会话时网页端自动切过去（打开页面时恢复出来的是空白会话也切），见 `docs/design-v4-map.md` 第 6 节。读数质量显示中文（缺数、疑似异常）。在 Docker 演示里验证：新建空白会话后触发告警，约 35 秒后自动切回值班会话；1920×1080、1366×768 截图正常。人工确认和处置选项卡片在变窄的聊天区里的显示还没有在真实流程里看过。
- **值守事件排队后被答成“重复投递”（2026-11-10）**：演示里黄色、橙色告警在智能体还在处理上一条时投递，排进 DSH 队列；第 2 轮的研判读的是矿井当前数据，已按橙色处置，第 3 轮轮到橙色消息时 `alert_status` 为已处理，智能体答“重复投递”。还查出一个隐患：“已处理”只看时间线先后，蓝色那轮用 19:32 数据写的研判排在 20:02 的黄色告警之后，把黄色算成了已处理（这次没漏是因为黄色消息在研判写入前就生成了）。改法：① 唤醒插件在值班会话忙（`agent.status` 不是 idle，或 inbox 里还有消息）时不投递，空闲后按当时的时间线判断，已被处理覆盖的告警写 `acked`（by=plugin，带 `handled_by`）、不再投递，只投新的；查不到会话状态时照常投递。② 固定流程和 guard 写进时间线的 note 带 `as_of`（引用记录里最早的取数时间），`alertStatuses` 只认 `as_of` 不早于告警时间的处理记录，没有 `as_of` 的旧条目不算（fail closed）；评测运行器造的“已处理”记录同步带 `as_of`。顺带修了 `composeMessage`：只要状态表里有已处理的告警（哪怕不在这条消息里）就附“重复投递”说明。测试：TS 新增忙时不投递、覆盖后写 acked、全被覆盖不投递、查不到状态照常投递、旧数据的研判不算、`evidenceAsOf`；guard 测试检查 note 的 `as_of`。**未做**：真实 DSH / Docker 演示里还没跑。
- **演示复看的四个问题（2026-11-10）**：
  ① 时间推进太快：导演认出处置 3 秒后就推进，不管智能体这一轮有没有结束（黄色时智能体刚批准加密监测、还在登记跟踪，矿井已推进 2 小时，橙色告警和跟踪到点一起来）；结束段每秒推 2 矿井小时、不等监听器。改为每一步（含结束段）等值班会话空闲、事件库没有待投递告警，结束段也等监听器心跳；唤醒插件每次轮询写 `duty-status.json`（`{busy, wall}`），文件不存在或过期不等。
  ② 红色后不继续：智能体发了红色预警、升级、撤人，认为“是否封闭属指挥部决定”没编封闭方案（下一轮编了也没提交），导演一直等“封闭”。技能红色那行原来写“火情需要封闭时”才编制，与第五版设计“红色自己撤人、封闭”不一致；改为红色一律编制并提交封闭方案（确认弹窗就是请示，物资缺口不是不提交的理由），`get_response_options` 无卡片时的说明、封闭方案工具说明、sealing-plan 技能同步。另外控制台导演进行中禁止手动注入、继续、快进（这次结束段里误点了旧的“开始演示”按钮，注入了 s4-escalation，封闭后又升红），按钮改名“手动注入事故”。
  ③ 关键测点“缺数”：模拟矿井封闭后密闭内的上隅角、回风巷安全监控测点断电（模拟真实情况），判据仍把它们当必需测点，报“必需测点失效”和缺数。改为火区台账显示本工作面已封闭时，`watch.yaml` 的 `sealed_exempt`（上隅角、回风巷，占位值）不算必需测点、不参与分级（`_warning.py`，封闭前的旧读数也不再用），报告写明“按束管监测”。新增自检样例 `check/s5-sealed-24h-power-off-not-failed`；其余检测样例的期望只多了 `sealed`、`sealed_exempt` 字段。
  ④ 确认弹窗看不到重点：DSH 卡片的说明块不保留换行，十来行挤成一段粗体，也没写要批的内容。`ApprovalSummary` 新增 `facts`（代码从记录或撤人、调度参数取：预警等级与触发条件、升级理由与条文、方案步骤、密闭巷道、物资缺口、待补充项、撤人人数与去向），弹窗正文改为“要做的事 / 确认人 / 要点 / 请把关 / 核对通过（N 项）：名称… / 依据记录”，浏览器端补样式保留换行、首行加粗（`client/src/approval-style.ts`，只用 `data-approval-key`、`role="group"` 两个稳定属性，升级 DSH 要重核）。
  **未做**：以上都只跑了单元测试和自检，还没在 Docker 演示里重跑一遍。
- **演示第二次复看（2026-11-10，Docker 演示 211016 跑到红色封闭后的结束段）**：
  ① 停在黄色：蓝色时已开加密监测，到黄色智能体不重复下发（对的），导演只认进入等待之后的处置，一直等。改为必做项（黄色加密监测、红色撤人和封闭）本次演示里 guard 执行过就算已到位；可选项仍只认这一级的。
  ② 通风图上蓝黄两个圈叠着呼吸：智能体按蓝色研判记录在工作面上标了一个，系统自动标记升黄后智能体的还是蓝的。`map_view mark` 拒绝系统已标的位置（提示用 focus），系统新标一个位置时清掉那里智能体的旧标记。
  ③ 复评一直说“台账里没有已下发的方案”：插件经 `ctx.tools.execute` 调 MCP 工具，rc.4 返回 `{ content, structuredContent }`（验证清单 #19），`callJson` 把外壳当结果，`found` 永远取不到。于是智能体问了一次处置开始时间，之后每次复评都沿用最早加密监测的 17:30、写成“指挥人员给出”，复评基线一直不对。改为取 `structuredContent`（`fire-loop/tool-value.ts`）。
  ④ 封闭后每 2 矿井小时一条“新”矿井报警、CO 越来越高：模拟矿井的报警平台只在查询窗口（24 小时）内扫，窗口之前就越限的报警从窗口第一个读数算起，窗口一滑动越限时间和报警编号就变，监听器每步都当成新报警；报的值其实是 24 小时前（封闭前）的读数。改为从场景开始扫，越限时间稳定。
  ⑤ 撤人没进事件时间线：撤人名单不引用记录，guard 挂不上事件（台账 `incident_id` 为空，确认弹窗也挂不上）。`EvacuationNotice` 增加可选的 `record_id`（研判的 `record_ids.personnel`），只用来挂事件，不核对时效、读不到也不拦撤人；技能红色那行写明照抄。
  ⑥ `check_mine` 在 211016 演示里查 1201：DSH 侧缺省工作面只读 `watch.yaml`。改为与监听器一样认 `RESCUE_WATCH_PANELS`，compose 给 dsh 也设上。
  ⑦ 跟踪间隔两处不一致：复评之后智能体每轮按默认 2 小时登记新跟踪（复评自己给的下次复评是 12 小时后），结束段 72 小时要唤醒约 36 次。`add_followup` 不给 `after_h` 时由代码定：最近一次复评之后没有新的对外动作下发，就用复评给出的下次复评时刻（固定流程把 `next_review_at`、`overall` 记进复评的时间线 note）；刚下发新处置或还没复评过，仍用默认间隔；返回带 `basis`。
  ⑧ 红色跳 20 小时会让到点 23:00 的跟踪晚 18 小时才投递：导演跳的途中有未到点的跟踪计划，先停在到点时刻，等智能体复评完再跳剩下的（总共跳的不变，演示会多等一轮复评）。
  ⑨ 同为橙色时每次复评都重问处置卡片，操作员跳过时智能体重发同样的方案（模拟矿井注氮量叠加）：`get_response_options` 带 `panel` 时按事件时间线判断（`priorChoice`）：本级方案已经下发、之后最近一次复评不是恶化 → 不给卡片，不重发方案；复评恶化 → 给追加卡片，这时点跳过就是不追加。技能橙色那行同步。
  修复只跑了单元测试，还没在 Docker 里重跑。
- **演示第三次复看（2026-11-10，Docker 演示 211016 从蓝色跑到封闭后 72 小时）**：
  ① 黄色阶段第一次复评问操作员起算时间：`query_last_issued` 只查封闭方案和防灭火方案。改为没有方案时取加密监测的开始时间（方案优先，之后再开的加密监测不挪起算时间）。
  ② 加密监测要人批准：按操作员决定，`start_monitoring` 由 C 级（超时自动放行）改为 **B 级，不弹确认**（仍经 guard 核对记录、写台账）；只有影响矿井现场的处置和对外通知要人确认。现在没有工具用 `on_timeout: approve`。
  ③ 回风巷一直标红、上隅角和回风巷一直“缺数”：封闭前越限的报警在测点断电后永远“未恢复”。模拟矿井的报警平台在测点因封闭断电时解除报警（消息注明原因；一般缺数仍不算恢复）；大屏关键测点在火区已封闭时把安全监控测点的缺数显示为“封闭断电”（束管、测温缺数照常）；复评把封闭后一直读不到数的安全监控测点排除在外，单独列出（`sealed_off`），不再一直算“观测时间不足 2 个”。
  ④ 跟踪计划分成两条线、复评成对出现：橙色登记新跟踪时黄色的旧跟踪没关。`add_followup` 改为取代本事件没完成的旧跟踪（写 `followup_done`，结果“被新的跟踪计划 … 取代”，返回 `superseded`）。
  ⑤ 研判写“实时报警 0 条”而大屏有 2 条矿井报警：实时报警只算规程红线规则。研判采集矿井报警平台未恢复的报警（采集清单新键 `mine_alarms_h`，TS/Python 两边一致；矿井没有报警平台时为 null），研判要点和摘要单独列一行“矿井报警平台未恢复的报警”。
  ⑥ 火区稳定后每次跟踪到点状态在 监测中/已稳定 间来回跳：计划内的跟踪到点不再把已稳定改回监测中（事件折叠 Python/TS 同改，监听器同改）。
  ⑦ 封闭方案注浆材料缺 1762.5 t：核对过，按现有公式和参数算没有错：采空区 250 m（走向长没有数据，用采空区漏风通道长度代）× 工作面 220 m × 采高 3 m = 165000 m³，× 注浆系数 0.05 = 浆液 8250 m³，÷（水土比 5 + 1）= 干料 1375 m³，× 1.5 t/m³ = 2062.5 t，台账现有 300 t。问题在估算口径：这是整个采空区一次性的注浆总量，却按“现在要备齐的物资”和台账库存比缺口；注浆实际是几天里持续制浆、按注浆站能力供应的。改为“持续供应”：注浆干料总量照算、列出台账现有量，但不算缺口、不进“有缺口的物资”和确认弹窗的物资缺口（`materials.yaml` 新增 `continuous_supply`，封闭方案和防灭火方案两个技能都改；砖、水泥、砂仍按缺口算）。总量公式和系数仍是占位值，待专家定。
  以上只跑了单元测试和自检，还没在 Docker 里重跑。
- **演示第四次复看（2026-11-10，Docker 演示 211016 从蓝色跑到封闭后 72 小时）**：第三次复看的修复在演示里都按预期生效。新发现：
  ① 演示结束后大屏像是停在封闭复评：导演封闭后推进 72 矿井小时就结束、暂停时钟，最后登记的跟踪不会再到点，但网页端看不到导演状态。模拟矿井新增 `GET /demo/status`（只有导演状态、结局和结束时刻，不含真值，不在 `/sim/` 下，用 mine_data 只读令牌），dsh 设 `RESCUE_DEMO_STATUS_URL`，大屏左栏顶部显示“演示已结束：…”，跟踪计划栏注明不会再到点。
  ② 加密监测重复下发：免确认后升黄时智能体又下了一次，矿井执行了两次。guard 改为同一事件里本工作面已开始加密监测就不再执行，返回原来那次的编号和说明。
  ③ 撤人名单第一次被拦（智能体把名单写成一句话）：`EvacuationNotice.persons` 可留空，给 `record_id`（人员记录）时 guard 按记录的受威胁人员和可达避灾路线填写（`evacuation_from_record`，预检和执行同一规则，确认弹窗人数按填好的算）；没有记录也没有名单时拦下。
  ④ 复评时智能体自己调 `check_mine`，结果没有事件基线，写成“由无升为橙色”：技能写明处理告警时不调 `check_mine`。
  未做：砖、水泥、砂不在模拟矿井物资台账里，调度单通不过校验，演示里看不到调度单。
- **演示第五次复看（2026-11-10）**：第四次的修复都生效（加密监测只下一次、撤人名单一次通过、演示结束提示）。新问题和操作员意见：
  ① 矿井报警的唤醒消息写着“先 check_mine”，智能体照做。按操作员意见：消息不再提 check_mine（改为直接要求完整研判），要不要先看最新状态由智能体按告警时间和矿井时间自己定；技能里撤掉“不要调 check_mine”，改为说明它没有事件基线；`check_mine` 卡片摘要写明“与正常状态比较”。上一节④的技能写法作废。
  ② 封闭方案丢了可选步骤：第 1 轮审核不通过，第 2 轮写方案时概述超过 100 字，直接退回缺省选择（不加可选步骤）。改为不合格时带着问题重做一次（`writeWithRetry`），仍不合格就沿用上一轮的选择，只有第 1 轮失败才用缺省。
  ③ 物资调运单：模拟矿井物资台账加上砖、水泥、砂（占位数量）；封闭方案脚本按台账算“需调运”明细（`dispatch.items` 里的物资，台账有多少调多少，平均分到各密闭处，送到巷道靠外的一端），`DispatchOrder` 的明细可留空、给方案审核记录 `record_id` 时由 guard 按记录填写（`dispatch_from_record`，预检与执行同一规则）。技能和流程的下一步说明里写明下发方案后提交调度单。
  ④ 撤人路线在 211016 回风巷之后画成直线：撤人路线按“节点位置 → 途经点 → 节点位置”画，映射表没有途经点。新增 `scripts/map/route_via.py`：把底图巷道线画到栅格上，沿线 A* 求节点之间的路径，化简后写进 `mine_map.yaml` 的 `via`（示意，未经现场核对；映射表在 var/map/，不进仓库）。
  ⑤ 发送/停止按钮改为在输入框右侧上下居中。
  ⑥ “最近记录”可点击：新增 `/rescue-map/record?id=`（只读记录目录、编号按格式核对），弹窗显示报告原文、结果（有按它生成的报告就显示报告）、证据汇总，上级记录可继续点开。矮屏（≤900 高）仍隐藏最近记录。
- **演示第六次复看（2026-11-10）**：调运单、封闭方案步骤、矿井报警不调 check_mine、撤人一次通过都按预期。按操作员决定再改：
  ① 橙色选项卡片由代码给出“不追加（只做必做项）”这一项（label_to_id 对应 null），智能体选项照抄、不自己加“跳过”或说明；点“跳过”仍按不追加处理。
  ② 调度单确认弹窗用中文名：方案的“需调运”明细带上地点名称，弹窗写“调运：砖 4915 块（主井底车场（N1） → 211016运输巷口（N2））”。
  ③ 启封评估只在指挥人员要求时做（智能体封闭 14 小时后自己发起过一次）。
  ④ 事件转为已稳定时，自动清掉智能体打的地图标记（只在转入那一刻清，之后再标的保留）。
- **演示第七次复看（2026-11-10）**：橙色“不追加”卡片、调度弹窗中文名、稳定后清智能体标记、不自己发起启封评估都按预期；记录弹窗显示正常。再改：
  ① 地图指令通道兜底：浏览器的地图标记停在蓝色（服务端已是红色，刷新后恢复）。心跳改成带序号的 ping 事件，浏览器 40 秒收不到任何事件、序号对不上（漏收指令）或连接被放弃时自己重连，重连先收快照。
  ② 撤人路线按已下发撤人通知引用的记录显示（6 小时内）：撤人后编封闭方案时重算的受威胁人员是 0 人，原来按最新记录显示，路线随即消失。
  ③ 处置选项卡片、现场条件卡片改成 ask_user 的题目格式（选项 {label}、multi_select）：原来给字符串选项，智能体第一次提问报错。label_to_id 移到卡片外。
  ④ “最近记录”补中文名（研判说明报告、时间线、事件复盘），并有测试覆盖所有技能的脚本和模板。
  ⑤ 模拟矿井的火区台账记实际下发时刻（原来对齐到步长整点，04:09 下发写成 04:00 封闭）。
  ⑥ 撤人提醒只在有实时红线报警、研判建议升级/已升级或红色时写：橙色研判时智能体照着研判要点里“撤人按预案立即执行”置顶写了撤人提醒。研判要点模板去掉受威胁人员行的这句，改放在“建议升级”后；主预设写明矿井平台越限报警、受威胁人数都不是撤人依据。
  ⑦ 人工确认卡片移到画面正中、压暗背景；右栏（智能体状态与对话）宽度改成与左栏相同（340px，窄屏 280px），先看对称效果。
- **演示第八次复看（2026-11-11）**：封闭方案编制后撤人路线仍按撤人通知的记录显示（第七次 ②）。再改：
  ① 提问卡片（处置选项、现场条件）也放到画面正中；居中卡片去掉外面一层（DSH 外层只当定位容器，不加底色、边框和压暗）。
  ② 右栏恢复原来的宽度（clamp(420px, 28vw, 560px)，窄屏 360px）：对称后对话区太窄。
  ③ 火区已封闭时，预警态势的等级下面注明“火区已封闭（时刻），读数为封闭后的束管监测；等级按读数判定，不代表火已熄灭”。这次演示封闭后约 32 小时束管 CO 回落，监听器连续 3 次检测正常后记“恢复”，左栏显示“无预警”；上一次封闭晚了约两天，演示结束前没到恢复，停在红色（上一次的事件库已被重置清掉，这一点是推断）。
  ④ Docker 演示的工作面名缺省 211016（`scripts/demo.sh`、`docker/compose.yml`），`DEMO_PANEL=1201` 换回原名；模拟矿井的 `/sim/state` 给出 `panel`，`story.py`/`demo_run.py` 没设环境变量时按它写提问。代码、测试、评测里仍用 1201（中性占位名，不把真实矿井的编号写进仓库）。
- **演示第九次复看（2026-11-11）**：蓝→黄→橙（两次选“不追加”）→红→封闭→已受控→监听恢复，全程没有工具报错；处置选项、现场条件卡片和人工确认卡片居中显示，现场条件卡片一次问成；橙色没有撤人提醒；封闭后撤人路线仍在，等级下的封闭说明显示正常。再改：
  ① 封闭方案的“火区封闭时间”：首次封闭时火区还没封闭，原来显示“缺失”，合规审核据此标了“需人工把关：火区封闭时间缺失且未列入待补充”。改为显示“尚未封闭（本方案为首次封闭）”，只有火区台账取不到时才写缺失（`compose.py` 的 `sealed_at_text`；只重新生成了封闭方案 compose 和 plan 模板的样例）。
  ② 撤人通知的确认弹窗：去向写避难硐室的中文名（原来只写节点编号“撤往：N8”），并列出名单里没有可达避灾路线的人（这次有 2 人在回风巷，路线“需人工研判”，弹窗里原来看不到）。
- **演示第十次复看（2026-11-11）**：第九次两项（封闭方案“尚未封闭”、撤人弹窗写避难硐室名称并列出无可达路线 2 人）在实际弹窗里显示正确；流程蓝→黄→橙（两次“不追加”）→红→封闭→已受控。再改：
  ① 智能体把只读工具写成 `mcp__actions__get_response_options`：没有登记的工具按 C 级弹了人工确认，操作员批了才报 unknown tool。现在 guard 上不存在的工具（不在分级表里；actions 的每个工具都在表里，有测试）由确认插件直接拦下、不弹确认，并提示去掉前缀。比原来更严，不放宽。
  ② 智能体仍会 mark 系统已标出的采面（工具拒绝、无害）：主预设写明事件所在工作面的采面只用 focus。
- **橙色处置卡片改版（2026-11-11，用户决定）**：注氮不再必做。卡片列出注氮（建议）、注浆、堵漏，多选；都不采取要在输入框亲手输入“不采取处置”再提交（代码逐字比对）；勾了处置又输入它算冲突、立即重问；点“跳过”或输入别的文字算暂不决定（先试过卡片里加“不采取处置”勾选项，会和具体处置同时勾上；又试过点跳过即不处置，容易误点；都按用户意见改掉了）。
  新工具 `record_response_choice`（参数 selected、custom、skipped，与 ask_user 的答案对应）：代码判断答案（处置 / 不处置 / 暂未决定 / 冲突），记进事件时间线；`get_response_options` 据此决定是否再问（暂未决定照常问；不处置之后复评恶化再问）；
  防灭火方案的注惰改为可选步骤，可选步骤的锚点不在方案里时顺着锚点链接上（没选注氮时注浆接在观测后）；`draft_firefighting_plan` 收到空选择报错（不处置不编方案）。
  演示导演按时间线里的“不采取处置”走新加的 `declined` 分支（到红色，占位，与只做 1 项同样的漏风突增）。演示脚本 `STORY_ORANGE_OPTIONS` 改为选项文字：story 缺省 `注氮`，demo_run 缺省 `注氮|注浆|堵漏`，`不采取处置`（在输入框输入）、空串 = 点跳过（暂不决定）。
  只重新生成了 firefighting-plan 的骨架期望、compose 和 plan 模板样例（新增“没选注氮”样例）。没有在真实演示里跑过。
- **演示第十一次复看（2026-11-11）**：橙色卡片改版后第一次实跑：蓝→黄→橙（输入“不采取处置”）→复评恶化后重问两次选注氮、三次“不采取处置”→红（预警、升级、撤人、封闭方案、调度单同一轮）→封闭→已受控→监听恢复；工具无报错，8 次确认全部批准，台账与时间线一致。再改：
  ① 研判说明（固定流程子智能体写的总述）在 5 次橙色研判里有 4 次写了“撤人按预案立即执行”（第七次 ⑥ 只改了模板和主预设，子智能体拿不到主预设的规则）。`explanation_options.py` 输出 `evacuation_reminder`（有实时红线报警、建议升级或红色才为 true，1.1.0；只重新生成了它的 3 个期望样例），为 false 时阶段提示词写明不要写撤人，`checkExplanation` 在总述、说明、理由里查到“撤人/撤离/撤出”判不合格（字段缺失按 false）。不合格时报告照旧只含事实部分，没有重试。
  未改、待定：橙色选“不采取处置”后导演已走向红色，之后复评恶化仍重问橙色卡片（这次多问了 4 次，其间选注氮导演不再认）；导演一次跳 12 小时只在跟踪到点处停，回风巷 CO 报警（08:00 起）19:58 才报给智能体；复评把“加密监测”当作处置开始（橙色不处置后显示“恶化”像是加密监测无效）；封闭后监听“恢复正常”是否因为断电测点没数据，没核实。
  复跑（2026-11-11，Docker 重建，`demo_run.py` 橙色输入“不采取处置”）：导演走完（蓝→黄→橙不处置→复评恶化重研判两次→红→撤人、封闭→结束）。3 次橙色研判说明都没有撤人字样，红色那次（`evacuation_reminder` 为 true）写了撤人；6 次研判说明全部生成，没有被新检查判失败。
  演示脚本两处修正：卡片的“提交”按钮先在提问卡片里按文字整段匹配找（网页端是英文 Submit，原来按包含匹配找“提交”会点到聊天记录里含“提交”的活动标题，一直点不动，停在橙色）；`STORY_ORANGE_OPTIONS=不采取处置` 时检查“没有下发防灭火方案且导演记为不处置”，不再要求防灭火方案已下发。
- **演示第十二次复看（2026-11-11）**：从截图和日志查出的问题，按用户决定改完，四次提交：
  ① 地图（`164e272`）：污染巷道的到达时间是烟气到巷道入口（风流上游端）的时间，原来标签取折点下标的中间点，两点的折线取到终点，211016 回风巷的“约 1.6 分钟”贴在了后段（实际约 13.6 分钟才到）。改为服务端按长度在入口一端往里 15%（占位）算标签位置（MapFigure 线的 `at`），文字“约 N 分钟到达”；撤人路线标签按长度取中点。颜色原来逐条画时只和已画的几条比，先画的总是“最晚”，除工作面外都成橙色；改为每次增删后整批重算（`recolorContaminated`）。地图包映射表（`var/map`，不进仓库）的回风路线原由 `route_via.py` 自动求途经点，加宽 4 个地图单位后相邻巷道的光晕连成一片，路线穿过岩体；改为手写：211016 回风巷 → 216 回风北石门下段 → 下部平巷 → 216 回风斜巷，回风井取斜巷上段。这是为演示选的示意路线，用户也不确定真实走向，待核实；不要对这几条用 `route_via.py --overwrite`。
  ② 确认弹窗与撤人通知的文字（`115f435`）：撤人通知的说明原由智能体写，演示里写成“预案撕人”原样发到矿井；改为给人员记录、名单留空时 guard 按记录生成正文（人数、去向、走不通的人、“补充，不替代、不延迟预案撤人”、依据记录），`EvacuationNotice.reason` 可空，只有另外点名时才用且必须写。封闭方案的密闭巷道写名称；升级理由改为“达到升级条件（不低于 红色（危险））”；核对项“方案类型、时效与红线”改名“方案类型与时效红线”（名称里的顿号让“核对通过（5 项）”看起来列了 6 项）。
  ③ 复评与跟踪（`3b0cffd`，解决第十一次的待定项）：台账里只有加密监测、没有方案时复评按“未采取处置”写（`effect.py` 的 `treated=false`，判据和结构化结论不变，结论“未采取处置：趋势恶化 / 上升减缓 / 停止上升 / 观测时间不足”，起算叫“观察起点”）；发布预警、升级、加密监测不算“新的处置”（只有防灭火方案、封闭方案、撤人、调度、启封算）；`add_followup` 不给间隔时再按当前预警等级限上限（研判记进时间线时带等级，封闭方案下发后不限，指挥人员给了间隔照给的）；选了“不采取处置”、复评恶化后再问卡片时，问题末尾说明原因（`applyPriorChoice`）。
  ④ 等级上限取值（`63421cd`，用户决定）：先用橙 2 小时，演示里橙色未处置要答 4 次卡片才到红色；为方便演示调为 蓝 8、黄 8、橙 6、红 1 小时（`followup.yaml`，占位，专家审定后改实际值；有测试保证等级越高上限不越长）。
  验证（Docker，`make demo-reset` 后 `demo_run.py` 和用户手动演示，橙色都选“不采取处置”）：导演走完；值班会话 11 轮、75 次工具调用无报错，6 次确认全部批准；橙色只停两次（观察 9.5 小时“观测时间不足”不问卡片；“未采取处置：趋势恶化”再问一次，卡片带原因、智能体原样照抄），随后升红，同一轮完成预警、升级、撤人、封闭、调度；撤人通知正文由代码生成；确认弹窗的升级理由、密闭巷道名称、核对项计数都正确；封闭后复评转为 12 小时节奏，已受控后事件标为已稳定。
  踩到的坑：`demo_run.py` 用 `uv run --with playwright` 时会下载最新版，网络不通就失败，用 `--offline --with playwright==1.65.0`（本机缓存）；模板里节里的变量会被当前项的同名字段遮住（复评逐点的 `effect` 遮住了外层 `effect.result`），逐点要用的字段放在点上。
  遗留（待专家）：复评至少观测 12 小时（`min_observe_h`），未处置时前 12 小时复评都是“观测时间不足”（2026-11-12 加了提前判恶化，见第十三次复看）；等级上限、标签位置比例是占位值；回风路线待按真实风流核实。智能体曾有一次把卡片里的“暂不决定”写成“暂未决定”，后一次演示原样照抄，未再出现。

## 代码复查（2026-11-11 起，演示版本代码完成后的整体复查）

按风险分轮：① 安全边界（guard、动作分级、红线、校验、令牌、审批插件、记录）② 数据契约与“数值只来自代码” ③ 编排主路径 ④ 技能脚本 ⑤ 模拟矿井、导演、地图、评测。

- **第 1 轮：安全边界（2026-11-11）**。没有发现能绕过红线或人工确认的路径。已核对无误：没登记的工具按 C 级、guard 上不存在的工具直接拦；`on_timeout` 全为 `escalate`（模块级断言）；预检不可用即拦；记录按哈希、来源、时效、工作面核对，方案从审核记录取并与上游方案记录比对，guard 下发前再校验；Docker 令牌分开且必填，记录目录和事件库在 guard、模拟矿井容器只读；真值只在 `/sim/` 下要控制令牌；沙箱没有令牌（验证清单 #21）。已改：
  ① 预警、升级的正文改由代码按记录生成（`validation.warning_message`、`escalation_message`），确认弹窗的要点就是“发出正文”，两边同一个函数。原来预警正文用智能体写的 `summary`，升级正文和级别用智能体写的 `reason`、`recommended_level`（违反约定 2），弹窗里看不到。契约 `WarningNotice.summary`、`Escalation.reason`、`recommended_level` 改为可选并忽略（兼容，契约版本仍 0.12.0）；升级通知的 `level` 改为记录里的预警等级；幂等键按发出正文算。
  ② 预检没有核对规则的指令按不通过处理（原来直接通过，新增 C 级工具忘了加核对时弹窗会写“预检已通过”）；测试保证分级表里每个工具都有核对分支。
  ③ 预警位置按工作面编号整体匹配（原来按子串）；撤人的名单核对和执行用同一次取到的人员定位（原来取两次，中间有人升井会抛 `KeyError`）；下发时间取不到矿井时间时用校验时的矿井时间，都没有才用系统时间并在说明里写明（原来直接用系统时间，会被当作处置开始时间）。
  **遗留（对接真实矿井前要处理）**：封闭方案、防灭火方案按步骤下发时前一步被矿井拒绝（如撤人需人工执行）后面照样下发，只有启封是 `ordered`；`ordered` 也不等异步执行完成（模拟矿井全部接受，演示碰不到）。没配令牌时服务不校验、没配写入令牌时只读令牌能调写入工具（Docker 强制必填，本机和真实部署要防漏配）。
  **待用户决定**：RL-088 能见度未知时放行（不符合“未知按不通过”，改了会在接上能见度数据前拦下所有进入灾区的方案；目前方案里没有进入灾区的步骤）；人工改预警等级时可以往低改，只要写了理由。
  **架构限制（已有 TODO）**：人工确认只在 DSH 插件里执行，guard 不知道一次调用是否经过确认，安全靠 actions 令牌只在 DSH 进程里。
- **第 2 轮：数据契约与“数值只来自代码”（2026-11-11）**。已核对无误：脚本运行的环境变量白名单、超时与整组终止、只能运行登记在本技能 `scripts/` 下的脚本；记录只新增、编号挡住路径穿越、两端规范化 JSON 共用样例；主预设和 10 个 SKILL.md 正文没有阈值；方案的现场条件由脚本从证据算，子智能体只能选步骤、巷道和写说明。已改：
  ① `draft_sealing_plan` 去掉 `human_inputs`、`leak_m3_min`、`target_o2_pct`：原来模型可以直接填现场条件（确认人也由模型写，填了以后弹窗的“待补充”消失）和注氮计算的输入。现场条件只经现场条件卡片（`field_choices`）；漏风量、目标氧浓度暂不收（用户选 A），注氮流量列为待补充、由确认人把关；技能脚本仍支持这两个参数，以后加卡片时用。确认弹窗要点加“人工确认的现场条件”（值和确认人）。
  ② 子智能体文字的数值核对（`ungrounded`）：日期、时刻也要在材料里出现（原来整段去掉，编造的“预计 14:30 到达”能通过）；中文数字加单位（“两小时”“三人”）判不合格。两个阶段的提示词同步写明。核对更严后阶段失败可能变多（失败时照旧只给事实部分或沿用上一轮选择），没有实跑验证。
  ③ 文档写明 `content_sha256` 只覆盖内容，`source`、`kind`、`panel`、`as_of` 不在哈希里，防篡改靠记录目录只有 DSH 进程能写。
  试过又撤回：结果记录的 `as_of` 改取输入里最早的，持续检测的 `check` 把基线（更早）作为输入，结果时间变成基线时间，持续检测结束不了（`test_watch_expectations` s1 失败）。保持取第一个输入，注释写明第一个输入必须是本次结果所依据的数据。
  **遗留（对接真实矿井前）**：采集时 `get_alarms` 的任何异常都记为“没有报警平台”（`null`），平台临时不通时矿井的未恢复报警会悄悄从研判里消失；应按 `get_capabilities` 区分不支持与失败，失败写进 `missing` 并在报告里提示。给整条记录加哈希或签名。
- **第 3 轮：编排主路径（2026-11-11）**。已核对无误：事件库只追加（`O_APPEND`）、建事件 `O_EXCL`、残行跳过；值班会话忙（在跑或队列有消息）时不投递，被覆盖的告警记 acked 并写依据，失败按退避重试；跟踪计划一个事件只留一条，默认间隔和等级上限由代码定；取不到矿井时钟时确认超时照常升级；处置选项卡片的答案和“要不要再问”由代码判断。智能体可给复评的 `since`（工具说明里是台账查不到时问指挥人员，结果注明来源），保持不改。已改：
  ① 告警是否已处理按告警原因判断（`alertStatuses`）：跟踪到点只认之后的效果复评（数据不早于告警）或该跟踪计划已完成/被取代；确认超时只认同一 `approval_key` 的确认结果；降级和不认识的原因只认 acked；异常、矿井报警不变。原来不分原因，跟踪到点时智能体正好用新数据做了研判，复评告警就被记为已处理、复评被跳过，跟踪计划停在“到点”再没人提醒。之后智能体刚研判完又收到复评告警时会再做一次 `review_effect`（有意），演示时值班会话可能多一轮。
  ② `query_last_issued` 在本工作面有未关闭火情事件时只看该事件里的下发（返回带 `incident_id`）：原来不分事件，同一工作面新事件的复评会把上一个事件的封闭方案当处置开始。演示每次重置台账，碰不到。
  ③ 监听器已见报警按第一次见到的先后保留最近 500 条（原来按编号排序截断，编号不按时间递增时新报警会被挤掉、再告警一次）。
  ④ 删掉唤醒消息里走不到的“重复投递”标注（投递前已过滤掉被处理的告警）。
- **第 4 轮：技能脚本（2026-11-11）**。已核对无误：爆炸三角形任一测点算不出即“无法判定”按有爆炸危险对待；效果复评任一恶化即恶化、观测不足不参与；受威胁人员无可达路线标“需人工研判”，人数合计由脚本算；跨技能复制的辅助模块有一致性测试；脚本都是纯函数。已改（用户同意）：
  ① 真实突升不再被当孤立尖峰剔除（`_quality.py`）：原来最新读数只能和前面的低值比，CO 从 10 突升到 200 时前两个读数被剔除（报“孤立尖峰，疑似干扰”），按模拟矿井的采样，束管 CO 突升晚约 2 小时、上隅角晚约 30 分钟才进等级（甲烷报警没受影响，只因尖峰判据要求读数大于 5、甲烷是百分比，碰巧挡住）。现在后面还没有读数的点不判尖峰（保留并注明“最新读数突升，待后续读数确认”），下一个读数仍高的台阶式上升也不算尖峰。
  ② 卡值按 A：读数长时间不变但达到该指标最低一级预警阈值时按保守处理保留并注明（满量程是火情中的常见情况，原来束管 CO 停在 1000 ppm 会整段排除、等级从红色掉下来）；低于阈值的照旧排除；监听的“必需测点失效”仍按卡值报。
  ③ 证据里没有 `SM-{panel}-UC`、`SM-{panel}-RET` 的 CO 读数时，数据质量里写明（原来悄悄缺失）。
  技能样例 102 个、基线评测各级首次到达小时数都没变（模拟场景读数平滑上升，碰不到这两种情况），s6 仍为无预警。新增 `mcp/contract/tests/test_skill_quality.py`。突升的最新读数立即参与定级，真实矿井里单点干扰可能让等级短暂升高（宁可多报；监听器仍要连续确认）。
  **遗留（对接真实矿井前）**：脚本按模拟矿井的测点编号找测点（`SM-{panel}-UC/RET`、`BT-` 前缀）并按“编号含工作面号”筛测点和巷道（7 处），应改为按证据里测点组的角色和测点的 `panel` 字段；`_quality.py` 的参数（尖峰倍数、卡值点数、校核容差）挪进 `references/` 并标注占位值。
- **第 5 轮：模拟矿井、演示导演、地图、评测（2026-11-12）**。已核对无误：地图路由都经 DSH 登录校验，调试路由只在开发配置下注册；`map_view` 只能用地图上的名称、标签取自记录或固定选项、不能标系统自动标出的位置、`clear` 只清自己的标记，图元只由代码生成；地图状态只读记录、事件库和 mine_data，不碰 `/sim`；导演推进前先暂停时钟、重置时整体换引擎对象，跨线程读不到半更新状态。已改：
  ① 演示导演识别升级改读监听器写的结构化字段 `level_to`（异常告警里等级比基线升高时写入），读不到再解析摘要文字；原来只解析“升为 X”的文字，监听器措辞一改导演就卡在推进。
  ② 评测里过时的 `recommended_level` 说明（`decisions.py` 示例与比较字段、`gen_expected.py` 注释、`docs/eval-consistency.md` 第 3 条待决定项标为已解决）。
- **复查小结**：五轮共 5 次提交（`69fbb1b`、`d723704`、`8249711`、`cce7124` 和本次）。**对接真实矿井前的待办**汇总：方案按步骤下发时前一步被拒或未完成要停（现在只有启封如此，且不等异步完成）；没配令牌时拒绝启动；报警平台取数失败与“没有平台”区分；记录整条加哈希；技能脚本按测点角色和 `panel` 字段取数；数据质量参数挪进 `references/`。**待用户决定**：RL-088 能见度未知是否拦截；人工改预警等级是否允许往低改；缺现场条件时封闭方案能否下发（之前就有）。**只有单元测试、没在 Docker 演示实跑**：第 2 轮更严的数值核对（研判说明可能更常缺失）、第 3 轮复评告警不再被研判顶掉（值班会话可能多一轮）、第 4 轮突升立即参与定级；下次演示前建议跑一次 `demo_run.py`。
- **复查后的三项决定（2026-11-12，用户同意建议）**：
  ① RL-088 能见度未知按不放行（与温度一致，符合“未知按不通过”）；目前方案没有进入灾区的步骤，不影响现有方案和演示；能见度可经现场条件卡片补充。
  ② 人工改预警等级只能往高调（写理由），不能低于记录的计算结果，guard 拒绝并提示先核实测点再重新研判；主预设、工具说明、契约注释同步。**待办**：做“人工标记测点故障”（带确认人，由代码在研判时排除该测点并在报告里写明），给降级一条有记录、可复核的路。
  ③ 缺现场条件时封闭方案仍能下发，但缺什么逐条列在确认弹窗的“请确认人把关”（合规审核列过的不重复），去掉“要点”里的“待补充”一行；注氮流量缺失的说明改为“漏风量和目标氧浓度目前没有输入途径（待专家定），注氮流量需现场确定”（第 2 轮后已没有 `leak_m3_min` 入口；技能脚本和 17 个样例文件同一句文字替换，样例 102/102 通过）。**请专家回答**：建临时密闭要派人到密闭位置作业，温度、能见度是否应对“建密闭”步骤也设红线（若是，按新增红线规则改 `redlines.py` 和 SKILL.md）。
- **演示第十三次复看（2026-11-12，用户同意建议）**：`make demo-reset` 后橙色输入“不采取处置”，演示停住。
  ① 导演误认升级：红色大跳途中跟踪计划到点，到点告警的摘要带着智能体写的期望“预警等级不升为红色”，没有 `level_to` 时退回文字解析，正则匹配到“升为红色”，导演停在红色等卡片；矿井实际仍是橙色，智能体按橙色处理，两边互相等。改为只有等级告警（`cause: anomaly`）算升级，正则排除“不升为”；`story.py` 的 `has_alert` 同样只看等级告警。
  ② 复评“坏消息早报、好消息等满观测期”（`effect-review`）：同一次复评观察 9.5 小时，3 根束管新出现 C2H4、CO 速率是起点前约 7.5 倍，结论却是“观测时间不足、不建议升级”（原来先判观测时长）。现在新出现标志气体随时判恶化；未满 `min_observe_h` 时速率明显加快（≥ 处置前 × `rate_ratio` 且加快不少于 `min_increase`，观测满 2 小时）也判恶化；已受控、需继续处置仍要满观测期。新参数 `early_worsening`（**占位值**：2 小时、1.5 倍、CO 1 ppm/h、TEMP 0.5 ℃/h；加快量下限是因为只按倍数时常态场景的读数抖动会被判恶化）。新增 5 个样例（新出现标志气体 1.5 小时、加速氧化 6 小时恶化、1 小时仍不足、缓慢氧化轻微加快仍不足、常态抖动仍不足），原有 5 个样例结论不变。
  ③ 导演支持“重新选择”（见 `design-v5-demo-flow.md` 第 5 节）：②之后橙色不处置的路线到点复评会判恶化、智能体重问橙色卡片，原来导演已往红色推进，不理会这次选择。
  **只有单元测试、没在 Docker 演示实跑**；下次演示建议橙色分别试“不采取处置”和只选一项。
- **演示第十四次复看（2026-11-12，用户同意）**：重跑演示（橙色三次“不采取处置”，导演“重新选择”按设计工作），封闭后等级红→橙→红。原因不是代码异常：红色只由束管乙烯 ≥ 2.0 触发（2.07），封闭注惰后被稀释到 1.82，研判为橙色，数小时后在密闭区积聚回 2.10，监听器报“升为红色”。
  ① **定级回差**（`fire-assessment`）：要降级时，上一次那一级的任一指标仍不低于“阈值 ×（1 − band）”就保持，全部回落到解除值以下才降；升级仍按阈值立即升。`band` 取 0.2（**占位值**，用户让先取合理值）。上一次：完整研判取本事件最近一次研判的预警等级记录（研判记进时间线时多记 `warning_record_id`，`warning.py` 新输入 `previous`），监听器取它的基线。结果新增 `level_raw`、`hysteresis`，保持时 `triggered` 写保持依据。新增 4 个样例（s5 封闭火区 40 小时保持红色、42 小时降为橙色、没有上一次时照常、监听器以基线保持）。按这次演示的数据，17:11 乙烯 1.82 ≥ 解除值 1.6，会保持红色。
  ② 封闭后不再弹橙色处置卡片（`priorChoice`）：火区封闭方案已下发、之后没有启封时，橙色不问。这次只因封闭后那次复评是“观测时间不足”才没弹；若是“恶化”（提前判恶化之后更容易出现）会问注氮、注浆、堵漏。
  ③ 新建 `docs/expert-review.md`（待专家审定清单，24 项），回差列为第 1 项，并请专家回答：是否按指标分别定回差、是否加时间条件、读不到数时是否保持、封闭注惰期间的稀释是否单独处理。
  **只有单元测试、没在 Docker 演示实跑。**

## 第六版进度（2026-11-17）：外部路径规划服务作避灾路线第二意见

设计：`docs/design-v6-route-service.md`（第一部分是发给提供方的对接方案，附录是我方实现）。提供方服务在内网 192.168.0.200:8080（`POST /api/v1/routes`、`GET /healthz`）。

- **R1 适配服务**：`mcp/route-planner`（包 `rescue_route_planner`，服务名 `route_planner`，端口 8107，取代 `DEFAULT_PORTS` 里第一版遗留的 workflow）。工具 `plan_escape_routes`（我方编号进出；逐终点调用，最多并发 4 个，每次超时 5 秒；校验返回路线首尾、连续性、越界和指标；按服务的排序规则选最优终点；带服务原始返回）和 `route_service_status`。输入错误抛 `ToolError`；服务不可用、超时、报错返回 `available: false`。`python -m rescue_route_planner.check <URL>`：接口一致性检查（健康检查、提供方示例的路线和三项指标、风速越界 400、不可达空结果，另有“提示”项：返回的新字段、未知字段是否报错、`length` 是否生效），2026-11-17 对提供方服务 12/12 通过。`check_patches.py` 检查任何预设都不挂 route_planner；`make up` 在设了 `RESCUE_ROUTE_SERVICE_URL` 时才启动它。
- **R2 技能脚本**：`fire-assessment` 新增 `route_request`、`route_compare`（参数 `references/route_service.yaml`，全部占位）。占位坐标的方法与设计草案不同：节点排单位圆（不用 numpy），每条巷道都加中间节点（附录 A.2）。对 s4 红色第 60 小时实测：工作面 9 人两种路线相同（经运输巷到避难硐室）；回风巷 2 人两种方法都没有路线（服务的烟中时间超过 900 秒）；把上限放宽到 5000 秒时服务给出穿过工作面回到进风侧的路线（烟中约 2033 秒），比较结果标为“能否穿烟由人判断”。待专家审定清单新增第九节 47–53 项。
- **R3 接入研判流程**：`run_fire_assessment` 在 `personnel` 之后加 `route_request` → `route_service`（证据记录，`collect_kind: routes`）→ `route_compare`，失败不中断；研判报告“受威胁人员”后加“避灾路线第二意见”一节（模板 `route_opinion`，没有时为 null，报告与之前相同）；流程结果多 `route_opinion`。证据插件配置 `routePlannerUrl`、`routePlannerToken`（`RESCUE_MCP_ROUTE_PLANNER_URL`、`..._TOKEN`）。
- **R4 地图**：左栏每组附“参考（外部服务，试验性）：比较说明”，图上另画蓝色细虚线“参考路线（外部服务，试验性）”；只认同一次研判的比较记录。
- **Docker**：`.env` 里设了 `RESCUE_ROUTE_SERVICE_URL` 时 `make demo-up` 启用 compose 的 `route` 组（`route-planner` 容器）。**只验证了 `docker compose config`，没有实际起演示环境跑过；地图上的参考路线也只有单元测试，没在浏览器里看过。**
- **踩到的坑**：占位坐标里出现 `-0.0`，Python 的 JSON 写 `-0.0`、JS 写 `0`，跨语言的记录哈希会对不上（TS 测试发现），脚本里统一换成 `0.0`。
- **遗留**：R5（按提供方答复改多终点、长度/高差、空结果原因、鉴权）、R6（矿方数据替换占位值）、R7（按比较结果改进我方算法）；`make_skill_cases.py` 不生成第二意见的样例（输入里有提供方服务的返回），personnel 样例重新生成后要手工同步 `route_request`、`route_compare` 的输入。

## 第七版进度（2026-11-17）：外部火灾推演服务作火灾蔓延第二意见

设计：`docs/design-v7-fire-sim.md`（第一部分是发给提供方的对接方案，附录是我方实现）。提供方服务在内网 192.168.0.200:5079（`POST /FireSimulation/GetMineFireSimulationResult`，接口说明 `/swagger/v1/swagger.json`；没有健康检查、鉴权和版本号）。

- **试用结论**（设计文档第 5 节）：服务是确定性的（示例输入的结果与提供方示例 `result.json` 逐字节相同），1 小时推演约 1 秒；CO 按约 2 m 网格、vol% 保留 3 位（分辨率 10 ppm）；巷道长度只按坐标算。**疑点**（已写进对接方案第 6 节第 1–5 条）：大火时风速几乎不变、没有风流反转；燃料 10–500 kg 结果相同、5000 kg 时 CO 反而骤降；47 条巷道中 9 条作火源时报 500；只能模拟巷道明火，不是采空区阴燃。
- **F1 适配服务**：`mcp/fire-sim`（包 `rescue_fire_sim`，服务名 `fire_sim`，端口 8108）。工具 `simulate_fire`（我方编号进出；校验输入，帧数上限 721；校验返回并压缩成每条巷道每帧的风速和最大 CO（ppm）、首次出现 CO 的时刻、峰值、反转、停风；原始返回只带 SHA-256 和字节数，因为服务是确定性的，复盘时用存档的 `provider_request` 重算核对）和 `fire_sim_status`（取接口说明代替健康检查）。不做判读。`python -m rescue_fire_sim.check <URL>`：2026-11-17 对提供方服务 11/11 通过或为提示。`check_patches.py` 检查任何预设都不挂 fire_sim；`make up` 在设了 `RESCUE_FIRE_SIM_URL` 时才启动它。
- **F2 技能脚本**：`fire-assessment` 新增 `fire_sim_request`、`fire_sim_compare`（参数 `references/fire_sim.yaml`，全部占位）；第六版的占位坐标移到共用的 `scripts/_layout.py`（`route_request` 输出不变）。对 s4 红色第 60 小时实测：CO 第 1 分钟出现在漏风通道，第 3、9、16 分钟依次达到 24 ppm 到回风巷、总回风巷、回风井，峰值 340 ppm（回风巷），除漏风通道外没有巷道达到 400 ppm；工作面在推演中没有 CO（火源放在漏风通道靠上隅角一端，CO 直接进回风巷），与我方“工作面受污染”不同，报告首句写明两边的源头。待专家审定清单新增第十节 54–61 项。
- **F3 接入研判流程**：`run_fire_assessment` 在避灾路线第二意见之后加 `fire_sim_request` → `fire_sim`（证据记录，`collect_kind: fire_sim`）→ `fire_sim_compare`，失败不中断；研判报告加“火灾蔓延第二意见”一节（模板 `fire_sim_opinion`，没有时为 null，原有报告不变）；流程结果多 `fire_sim_opinion`。证据插件配置 `fireSimUrl`、`fireSimToken`、`fireSimTimeoutMs`（`RESCUE_MCP_FIRE_SIM_URL`、`..._TOKEN`）。
- **Docker**：`.env` 里设了 `RESCUE_FIRE_SIM_URL` 时 `make demo-up` 启用 compose 的 `fire` 组（`fire-sim` 容器），可与第六版的 `route` 组同时启用（`demo.sh` 的 `COMPOSE_PROFILES` 改为按设置拼接）。**只验证了 `docker compose config`，没有实际起演示环境跑过。**
- **踩到的坑**：在仓库根目录对 `skills/`、`scripts/` 下的文件跑 `ruff format` 会用缺省行宽 88（配置在 `mcp/pyproject.toml`），要在 `mcp/` 目录里运行；仓库本来也没有整体按 `ruff format` 排版，不要对已有文件跑。
- **F4 地图**（设计文档附录 A.7）：推演中 CO 到达的巷道画成紫色细线（已达危险浓度为品红），与我方污染巷道并列；左栏“火灾蔓延第二意见”一节带回放滑块和“播放”，按记录里的到达、危险时刻逐分钟显示，回放时我方污染巷道调淡。只认同一次研判的 `fire_sim_compare` 记录。用临时测试架（真实地图包 + 自检样例记录，Playwright 截图）看过 s4 实测和人为倒灌两组：第一版的细线压在污染巷道上看不清，改为加粗并在回放时调淡污染巷道。**没有在 DSH 网页端、Docker 演示里看过。**
- **遗留**：F5（按提供方答复调整；推演结果作为路径规划服务的烟雾输入）、F6（矿方数据替换占位值）。

## 外部服务改为“外部为主、我方兜底”（2026-11-17，第六版 R8、第七版 F7）

用户要求：默认只显示外部服务的计算结果，外部服务不可用时才显示我方结果，并在显示时区分。用户另定了三件事：撤人通知里的路线也用外部路线；路径规划服务正常返回“无路线”时照外部结果显示无路线（不改用我方路线）；火灾推演从橙色起请求（原为红色）。

- **对接文档先改**（发给提供方）：`design-v6-route-service.md`、`design-v7-fire-sim.md` 第一部分按新策略改写（目标、分工、新增第 3.1 节显示规则），第 6 节重排，新增“必须确认”的问题（空结果原因、结果能否直接用于撤人、可用性、响应时间、错误含义、确定性；推演另有阴燃阶段能否用明火模型）；给路径规划方的精简版 `route-service-integration-plan.md` 同步。条号变了，代码和清单里引用的条号跟着改。
- **选择在技能脚本里做**：`route_compare` 1.1.0 每组给出 `display`（来源、原因、路线）和 `display_lines`；`fire_sim_compare` 1.1.0 给出 `display_source`、`display_lines`（只写推演结果），可选输入 `route_compare`（按显示的路线核对危险巷道）。原来的比较照旧存档。
- **报告**：人员行按 `route_from_compare`、`spread_from_sim` 决定写不写我方路线和到达时间（写时标“本系统计算/估算”）；“有害气体蔓延”“避灾路线”两节列显示用的整句。
- **地图**：每组只画一条路线（外部 `route` 绿色、我方 `route_local` 蓝色），去掉 `reference`；推演可用时不画我方污染巷道；左栏写来源和原因。记录类型名改为“避灾路线选择”“火灾推演判读”。
- **撤人通知**（guard）：`validation.evacuation_routes` 经 `RecordStore.derived` 找同一次研判的 route_compare，按 `display` 填路线；正文写路线来源、穿烟时间，依据加 route_compare 编号；确认弹窗的“撤往”写来源。智能体调用方式不变。
- 测试：自检样例重新生成（`make_fire_sim_cases.py` 加 `--reuse`，沿用已存的提供方返回，不调用服务；`fire_sim_request` 的 s5 橙色改为需要，新增 s6 无预警不需要）；报告模板样例；`test_validation_records.py` 新增撤人通知按显示路线取；一致性测试加报告和通知的检查；`map-evacuation.test.ts` 改写。待专家审定清单改第 53、54、60 项，新增第十一节 62–64 项。
- **只有单元测试和一致性测试，没在 Docker 演示或浏览器里看过**；没有配置外部服务时（当前演示），图上的路线从绿色变成蓝色并标“本系统计算”，报告人员行多“（本系统计算/估算）”。

## 大屏卡片挪到下方（2026-11-17）

用户要求：弹窗（人工确认卡片、处置选项和现场条件提问卡片）不要挡住地图。原来放在画面正中（第五版演示第七、八次复看），改为放在大屏下方、左右栏之间：底边离屏幕底部 16px，水平居中在左右栏之间的空隙（收起左栏或右栏时跟着变），宽度最多 820px 且不超过空隙，高度上限从 82vh 改为 50vh（占位，内容更长时在卡片里滚动）。有卡片时底部的提示条挪到顶栏下面，免得被盖住。只改 `dsh-bundle/client/src/hud.ts` 的样式。
- TS 测试通过、浏览器端已重新打包；**没在 Docker 演示或浏览器里看过**（演示镜像要 `make demo-up` 重建）。确认说明很长时批准按钮可能要滚动才看得到，实际看后再定高度上限。

## 界面统一写“灾害推演”“路径规划”，来源只用颜色区分（2026-11-17）

用户要求：不论显示的是智能体（本系统）的计算结果还是外部服务的结果，界面上都写“灾害推演”和“路径规划”，只用颜色区分来源。
- 颜色（和原来地图上的线一致）：路径规划 外部服务绿、智能体蓝；灾害推演 外部服务紫（危险浓度品红）、智能体红到橙。左栏两节末尾各有一行颜色说明（色块 + “外部服务 / 智能体计算”）。
- 地图标签：路线“路径规划 · 约 N 米 / 约 N 分钟”；蔓延“灾害推演 · N 分钟到达”（推演）或“灾害推演 · 约 N 分钟到达”（我方估算）；无路线的人员组“N 人 · 无可行路线，需人工研判”（不再写外部服务给的原因）。
- 左栏：“有害气体蔓延（外部火灾推演，试验性）/（本系统估算）”改为“灾害推演”一节；没有推演或推演不可用时列我方估算的受污染巷道和到达时间（原来只有一句说明），不再显示“外部推演不可用”的原因。每组人员下分两行“灾害推演：…”“路径规划：…”，按来源着色；去掉“路线：外部路径规划服务（试验性）/本系统计算（原因）”一行和底部讲来源的说明。
- 右栏记录名：路径规划请求、路径规划、灾害推演请求、灾害推演；证据（路径规划）、证据（灾害推演）。
- `fire_sim_compare` 的 `display_lines`（界面和报告用）不再写“避灾路线（外部路径规划服务）”的来源，存档用的 `lines` 不变；版本 1.1.1，样例同步改。报告模板其余地方的来源说明没动（用户只要求界面）。
- 记录、状态接口里的 `source`、`reason` 照旧（点记录可查原因）。`make check` 通过；**没在 Docker 演示或浏览器里看过**。
- 文档：第六版附录 A.7、第七版附录 A.7/A.9 和 `docs/architecture.md` 已按新界面改写；发给提供方的文件不改（提供方不关心我方怎么集成显示）。

## 无预警时不登记跟踪，到点改为重新研判（2026-11-18）

用户决定：无预警时不登记跟踪；跟踪到点（无预警时）改为重新研判，不做效果复评。
- “平静”= 最近一次研判为无预警，且事件里没有下发过处置（方案、撤人、调度、启封；发布预警、升级、加密监测不算）。下发过处置的不算平静：封闭火区后读数回落、按读数定级为无预警时，火区仍要长期观测，跟踪和复评照常。判断在 `src/incident/quiet.ts`（`TREATMENT_TOOLS` 等从 `incident/index.ts` 挪过来）。
- `add_followup`：平静时不登记，返回 `registered=false` 和原因（给了 after_h 也不登记）；登记成功的返回加 `registered=true`。
- 唤醒消息：平静时跟踪到点改为“请重新研判（run_fire_assessment），不做效果复评；用 complete_followup 记下研判结论；研判仍为无预警时不再登记跟踪”。告警是否已处理的判断没改：跟踪到点仍靠复评或 complete_followup 覆盖。
- `review_effect`：台账里什么都没下发、最近一次研判为无预警时不复评，返回说明改为重新研判（上一条的“从最近一次研判起观察”只留给非无预警的情形）。
- Docker 演示实测：s4-escalation 自由运行（1200 倍速）时，无预警研判之后没有登记跟踪（改之前这里会登记，到点后复评）；导演推进时蓝色研判后加密监测下发、跟踪照常登记。自由运行时蓝色以上的加密监测都因引用的研判“已过时”被拒（智能体一分钟里矿井时间走了几小时，反复重新研判），这是测试方式造成的，与本次改动无关；演示要用导演推进。平静时跟踪到点的分支没有在演示里自然出现，只有单元测试。
- `fire-incident-management` SKILL.md：按等级处理的表加“无预警”一行，跟踪到点、登记跟踪两处加说明。测试：`incident-wake.test.ts` 加 2 个、`effect-flow.test.ts` 加 1 个；TS 356 个通过，`make check` 通过。

## 无预警时复评不再问操作员起算时间（2026-11-18）

用户要求处理演示中看到的问题：等级为“无预警”时跟踪计划到点，`review_effect` 在指令台账里找不到任何下发（方案、加密监测都没有），返回“请问指挥人员处置是什么时候开始的”，智能体于是自己编了一张“复评起算时刻”的卡片问操作员（违反“选项、卡片由代码给出”）。
- `review_effect` 起算时间的取法加一档：台账里什么都没下发时，取事件时间线里本工作面最近一次研判（`assessment` 条目）的依据数据时间作观察起点，`treated=false`，起算来源写“未采取处置，从最近一次研判 <记录编号> 的数据时间起观察”，结论按“未采取处置：……”写（effect.py 原有逻辑）。连研判都没有时返回说明让智能体先研判，不再让问起算时间。
- 代码：`src/incident/timeline.ts` 新增 `lastAssessmentNote`，`src/fire-loop/effect.ts` 的 `lastAssessment` 选项；工具说明、`fire-incident-management` 和 `effect-review` 的 SKILL.md 同步改为“不要问指挥人员起算时间”。测试：`effect-flow.test.ts` 加 2 个、新增 `incident-last-assessment.test.ts`；TS 测试 353 个通过，`test_fire_loop_parity.py` 里“台账里什么都没有”的断言改为新说明，`make check` 通过。
- Docker 演示实测（`make demo-reset` 后跑 s4-escalation，真实模型）：无预警研判后智能体登记跟踪，到点后复评直接完成——“未采取处置：观测时间不足，暂不下结论；观察起点（未采取处置）2026-11-02T16:46:38（未采取处置，从最近一次研判 RP-20261102T164638-hcq5se 的数据时间起观察）”，没有弹出起算时间的提问，智能体接着登记了下一次跟踪。
- 只改了起算时间的取法；无预警时该不该登记跟踪、跟踪到点该复评还是重新研判，没有动。

## 大屏：启动全屏地图、面板滑动、顶栏三段（2026-11-18）

用户要求：启动后默认全屏地图，收到预警时退出全屏、两侧面板打开、地图飞到事故地点；面板和弹窗显示/隐藏时滑进滑出；顶栏标题居中、左边状态和时间、右边操作按钮；只显示必要的按钮，其余收进“»”。
- 启动时三块面板都收起，不再记住上次的开关（原来存在 localStorage）。新告警（`incident.latestAlertId` 变了，第一次只记下）：退出全屏、提示“收到预警：工作面 · 等级 · 阶段”、镜头飞到 `incident.anchor`（状态接口新增，取自映射表）。原来 Node 侧自动叠加发的 focus 指令去掉（会在全屏时先飞一次，面板出来后被挡住），改由网页端做；`map-autopilot.test.ts` 相应改写，`map-state.test.ts` 加 anchor 断言。
- 地图让开的区域按面板展开后的最终位置算（不含 transform，面板滑动时就飞向最终位置），下方有确认或提问卡片时底边算到卡片顶上（1366×768 下原来会飞到卡片后面）。
- 顶栏：左边矿井时间、本机时间、预警等级（新增，全屏时也看得到）、矿井数据、监听；中间 LOGO、标题、图名；右边“全屏地图/展开面板”“复位”“»”（菜单里：矿井信息栏、智能体卡片、对话区、会话列表、图例、倾斜视角）。
- 滑动：见 `design-v4-map.md` 第 6 节。
- 第二轮（用户采纳建议）：顶栏闪烁“N 条待确认 · 待回答”，点它展开面板；预警升到橙色、红色时画面四周闪一圈等级色；快捷键 `F` 切换全屏、`Esc` 收起菜单/会话列表/图例；事件转为已稳定或已关闭且没有卡片，2 分钟（界面参数，占位）后自动回到全屏地图。
- Docker 演示重建后实测（`make demo-reset` 后跑 s4-escalation，真实模型）：启动全屏 → 第一次告警退出全屏、面板滑出、飞到 211016 采面 → 蓝、黄、橙、红逐级提示，橙、红各闪一次 → 确认卡片在下方。发现智能体等回答时运行状态是“工作中 · 向操作员提问”而不是 waiting_question，“待回答”改为看页面上有没有提问卡片。快捷键、待确认标记、自动回到全屏用改写状态接口的页面验证（自动回到全屏的计时临时缩到 3 秒）。
- 用户演示时没看到面板滑动（系统没开“减少动态效果”）。测量（无头 Chromium、软件渲染、1920×1080，约 20 帧/秒）：原来 0.38 秒、前快后慢的曲线在 150 毫秒内就走完 84%，只有两三帧在动，收起时还先卡 100 毫秒再跳，看起来像没滑。改为 0.55 秒、前后对称的曲线，滑动期间去掉面板的毛玻璃（每帧要重新模糊背后的地图），新告警时等面板滑完再飞镜头；改后位移均匀分布在约 0.45 秒里。只重建了 dsh 容器。
- 用户在本机（Ubuntu + Firefox 158）看仍然直接弹出：浏览器诊断显示 `prefers-reduced-motion: reduce` 为真（GNOME 关掉了动画），而我们跟随了这个设置。改为不跟随系统设置，要关掉动态效果用 `localStorage['rescue-map:no-motion'] = '1'`；原来的规则还顺带关掉了 DSH 组件自己的动画，现在只作用于自绘部分和面板外壳。模拟“减少动态效果”的浏览器里验证：默认滑动，设了开关后直接显示。
- 演示中顺带看到（未处理，不属于界面）：等级为“无预警”时跟踪计划到点，复评因没有下发过处置缺“处置开始时间”而失败，智能体向操作员提问“复评起算时刻”。
- 检查：TS 测试 350 个通过、类型检查通过、浏览器端已重新打包。第一轮用无头 Chromium 打开正在运行的 Docker 演示网页端，把响应里的旧 `client.js` 换成新的（**没有重建或改动容器**），看了 1600×900 和 1366×768：启动全屏、展开面板滑动、“»”菜单、模拟新告警（改状态接口里的告警编号并补 anchor，容器里的 Node 侧还是旧的）、卡片关闭的克隆滑出。**真的告警、真的批准/提问卡片关闭没看过**，演示镜像要 `make demo-up` 重建后再看。

## 演示环境部署到服务器、控制台“新一轮演示”（2026-11-19）

用户要求：演示要能部署到服务器（先公网，以后内网），必须本机打包再拷到服务器；一场接一场地反复演示（同一时间一场）；重来最好在控制台点按钮；地图随包发布。说明见 `docs/deploy.md`。
- **发布包**：`make demo-pack` 构建镜像，连同 Caddy 镜像导出，和 `compose.yml`、`Caddyfile`、`rescue-demo.sh`（就是 `scripts/demo.sh`，按所在位置区分仓库/发布包）、地图包 `map/`、`env.example`、部署说明打成 `var/dist/rescue-demo-<版本>.tar`（约 374 MB，不含密钥）。`docker/compose.yml` 改为只引用镜像（`:${DEMO_TAG}`）、路径用 `DEMO_DATA`/`DEMO_MAP_DIR`，构建移到 `compose.build.yml`；技能改为随镜像发布（python 镜像也 COPY skills），不再挂载。服务器上配置和数据在包旁边的 `rescue-demo-data/`（`.env` 在数据目录外，归档服务看不到模型密钥），升级换包不动数据。容器以 uid 1000 运行，脚本用一次性容器改数据目录属主（服务器常以 root 运行）。
- **对外入口**：`.env` 设 `DEMO_PUBLIC_URL`、`DEMO_AUTH_PASSWORD` 时起 `proxy`（Caddy 2.8.4，compose `public` 组）：域名自动证书、IP 自签证书、内网可用 http；端口按地址生成 `proxy-ports.yml`。DSH 的 `--public-url`/`--trusted-host` 改由环境变量给。踩到的坑：①浏览器用 IP 访问不发 SNI，Caddy 选不出证书、握手失败，要设 `default_sni`；②登录口令（basic auth）和控制台的控制令牌都用 `Authorization` 头，套了口令控制台请求全被挡，所以 `/console`、`/sim/` 只靠控制令牌（128 位），网页端才套口令；③DSH 的 `?token=` 每个进程随机生成、只用来换 30 天的 cookie（签名密钥在 `dsh-home/.credentials.yaml` 持久），所以**每次重启登录链接都变**，已登录的浏览器不受影响。
- **新一轮演示**（不用 Docker 套接字，避免把宿主机 root 交给容器）：dsh、actions、sentinel 由守护进程 `rescue_contract.rounds` 起服务，看控制目录 `round.json`；新服务 housekeeper（`rescue_mine_data_sim.housekeeper`，端口 8109，挂载整个数据目录）把轮次加一、等各服务报告已停、把上一轮的记录/事件/值班会话/运行目录/台账/审计/工作区和 DSH 会话**移到** `archive/round-<轮次>-<时间>/`（只移目录里的内容，目录本身是挂载点）、让模拟矿井回到常态（导演复位）、再放行；服务没按时停下就不归档、直接放行。控制台“演示轮次”区块经 `/sim/demo/round`（控制令牌）调用，显示轮次、进度和当前的网页端登录链接（守护进程从 DSH 输出里识别，记在 `login-dsh.json`）。`make demo-reset`/`rescue-demo.sh reset` 改为同一套归档（原来是删除，且会删掉 `dsh-home`）。大屏状态带 DSH 的启动编号 `boot`，变了就刷新页面；DSH 重连会重新挂载客户端插件，编号存在 `sessionStorage`。
- 检查：Python 测试（新增 `test_rounds.py` 6 个、`test_housekeeper.py` 5 个）、TS 测试、lint 通过。本机 Docker 实测：①HTTPS + IP（`https://192.168.0.163:8443/`）：不带口令 401、控制台 200、`/sim/` 不带令牌 401；无头 Chromium 经口令登录打开大屏，点“开始演示”（真实模型）到智能体研判出 6 条记录后点新一轮：约 2 秒完成，记录、事件、会话、运行目录进归档，矿井回到常态、导演待开始，大屏自动刷新（用 `window` 标记确认）且仍是登录状态。②**模拟一台新服务器**：删掉本机的版本镜像，把发布包解压到空目录，只用包里的脚本（`env -i`，不读仓库 `.env`）：第一次生成配置文件后退出，填好后导入镜像、全部健康；`http://…:8088/` 模式下同样过了新一轮演示和 `reset`。
- **未做**：没有在真正的云服务器和域名证书上跑过（只在本机用 IP 自签和 http 模拟）；内网部署要换本地模型，模型配置还没进发布包；ARM 服务器要另外构建；归档不自动清理。

## 发给提供方的文档只写测试、问题和配合事项（2026-11-17）

用户要求：发给提供方的文档不写我方怎么设计、怎么集成（版本修改、显示规则、兜底、调用流程、占位输入的生成方法、超时等），只写集成对方服务时做了哪些测试、发现了哪些问题、需要对方配合做哪些修改；也不应让对方知道我方集成细节。
- 发给提供方的文件：`docs/route-service-integration-plan.md`（重写）、`docs/fire-sim-service-integration-plan.md`（新增），各三节：测试情况、需要贵方配合的事项（必须确认／希望支持／请说明，编号与设计文档第 6 节相同）、后续。问题的理由改成不涉及我方集成的说法（如去掉“超时即改用我方路线”“界面显示……”“烟雾结果可以作为路径规划服务的输入”），测试表里“与我方路线相同”“拆成 22 段”这类集成细节去掉。
- Word 文件 `~/Downloads/{路径规划,火灾推演}服务对接问题清单.docx` 按同样内容重新生成（旧版另存 `*.旧版-20261117.docx`）。
- `design-v6-route-service.md`、`design-v7-fire-sim.md` 改为内部文档，不再标“第一部分可直接发给提供方”；第 5、6 节与对外文件同步维护。
