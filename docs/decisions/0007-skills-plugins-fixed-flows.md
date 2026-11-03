# 0007 第二版：专家经验以技能为载体，固定流程、证据、知识库做成 DSH 插件，指令发布仍走 actions

状态：采纳（2026-11-03）。部分取代 0006（决定 4、5 和“考虑过的方案”里对 DSH 插件、DSH 工作流的结论）和 0003（自查工具放在 kb）；其余部分仍有效。

## 背景

ADR 0006 把确定性流程写成 `mcp/workflow` 服务，研判类评测由此稳定下来（`s2-assess-1201` 等 6 个用例 3/3，token 和耗时降了一个数量级）。但还剩三个问题：

1. **专家改不了算法。** 判据、算法在 `fire_model`、`ventnet`、`dispatch`、`workflow` 四个服务的 Python 代码里，技能只是草稿、不能运行。专家要改一个判据得找开发。上一版（dsh-rescue-agent）的技能是标准格式（SKILL.md + 脚本 + 模板），带专家资产（爆炸三角形、应急能力评估），这一点比本项目好。
2. **多步流程仍由模型编排。** 封闭方案要经过“方案编写 → 合规审核”的循环，总指挥在上下文里反复携带完整方案 JSON，`s4-sealing-plan` 单次 170–340 万 token，全部通过不稳定。合规 Agent 凭记忆引用未检索的条文。
3. **没用上 DSH 原生能力，查条慢。** 技能不能运行脚本；没有持续检测（独立的值守进程在 DSH 之外，发现异常不能唤醒智能体）；`get_article` 依赖 RAGFlow 切片，单次 15–65 秒，跨切片的条文会被截断。

ADR 0006 当时不采用 DSH 插件写流程，理由是“领域逻辑会分散到 TS 和 Python 两处”和“DSH 工作流的子 Agent 不能指定预设”。之后的验证改变了这些前提：

- #19：插件可以用 `ctx.tools.execute` 调工具（经过审批和审计），用 `ctx.workflowEngine.start` 运行仓库里固定的脚本、要求结构化输出、用 `tools.guard` 限制阶段子 Agent 只能提交结果。只要插件只做编排、算法不写在 TS 里，就不存在逻辑分散的问题。
- #20：插件能用 `ctx.jobs.start` 登记后台任务，检测期间不经模型、结束时由 DSH 唤醒智能体询问操作员；网页关闭、空闲 20 分钟都不影响。
- #22：插件能在 DSH 进程里起子进程运行 Python 技能脚本，不受 bash 沙箱限制，结果写进沙箱写不了的记录目录。
- #23：唤醒的这一轮因模型接口错误失败时，provider 重试和插件补发都能补救。
- 实验 0001 第 0 步：开放 bash 后，沙箱拿不到令牌、写不了服务端目录，但读不受限、批准提权后可写、出站不受限。

完整设计见 `docs/design-v2.md`（第五稿，定稿）。

## 决定

1. **技能是专家经验和算法的唯一来源。** `skills/<技能名>/` 下：SKILL.md 写流程和解读要点，`scripts/` 写算法（纯函数，文件进、文件出，不访问网络和凭据），`templates/` 写报告格式，`references/` 写参数和依据，`tests/cases/` 放自检样例。现在 `fire_model`、`ventnet`、`dispatch`、`workflow` 的算法和判据迁入技能脚本，CI 和镜像构建时跑全部样例。
2. **同一份技能两种用法。** 模型经 DSH 技能读 SKILL.md，在沙箱里运行脚本做试算和解释，结果只作参考；证据插件在 DSH 进程里运行同一批脚本，结果存为记录，只有记录能被报告和指令引用。
3. **证据、固定流程、知识库、会话入口做成 DSH 插件**（`dsh-bundle` 里，DSH 类型仍只在 `src/adapter/dsh.ts`）：
   - **证据插件 `rescue-evidence`**：按技能的采集清单经自带的 MCP 客户端读矿井数据，运行技能脚本，存记录（取代研判快照），以及风险检测 `check_mine`（一次性；持续检测登记为 DSH 后台任务，发现异常时唤醒智能体询问操作员）。
   - **固定流程插件**：`run_fire_assessment`、`draft_sealing_plan`、`review_effect`。步骤由代码决定；需要模型的阶段用 `ctx.workflowEngine.start` 运行插件里固定的脚本，阶段指令取相应技能的 SKILL.md，只能在给定清单里选择，说明文字里的数值必须能在本次记录里找到；报告由模板生成。
   - **知识库插件 `rescue-knowledge`**：以上一版插件为基础。规程按条建索引，`kb_get_articles` 按条号批量返回完整条文，不经 RAGFlow、不由模型拼接；`kb_search` 走 RAGFlow。
   - 判断做成插件还是 MCP 的标准：只在 DSH 里用、要读写会话工作区、要设置页、要调用其他工具或起子 Agent、要交付文件或挂在工具管道上的，做成插件；DSH 之外也要用、在外部平台、要与 DSH 进程分开持有状态的，做成 MCP。
4. **指令发布仍只有 actions 一个出口**（ADR 0002 不变）。actions 和模拟矿井仍是 MCP（Python）；指令改为引用记录（`record_id`），发布前按记录重算 `content_sha256` 并核对现场条件、等级和时效。红线引擎仍在 `rescue_contract/redlines.py`（ADR 0003 的“只实现一次”不变），起草期自查从 kb 移到 actions 的只读工具 `precheck_plan`。
5. **一个主预设 `rescue-commander`**，挂技能、bash/文件（沙箱 workspace-write）、ask_user、todo、present、后台任务、上述插件和 actions；不挂 mine_data（读数经证据插件，留有记录）、delegate、联网、知识库写入。`rescue-plan`、`rescue-compliance` 改为固定流程里的阶段。
6. **DSH 自带工作流的用法改为“插件运行仓库里的固定脚本”**：不挂 `dsh-tool-workflow` 让模型现写脚本（0006 决定 5 的这部分仍成立）。
7. **安全前提**：插件起的技能脚本子进程不受任何限制、默认继承 DSH 进程的密钥（#22），所以脚本是受信代码，只经评审和 CI 进入技能库；`runScript` 按白名单传环境变量、设超时；部署时技能目录只读挂载、出站白名单兜底。令牌按服务和调用方拆分，DSH 不持有 `/sim` 控制令牌。
8. **唤醒失败两道补救**（#23）：provider 重试把 `STREAM_CLOSED` 列为可重试；证据插件在唤醒这一轮以 error 结束时带退避补发通知。

## 考虑过的方案

- **保持 ADR 0006（代码工作流在 MCP）。** 研判稳定，但专家改不了算法，封闭方案循环仍由模型编排、token 高，持续检测在 DSH 之外，见“背景”。
- **技能驱动：模型按 SKILL.md 自己串步骤、运行脚本**（实验 0001 的 B、C 组）。第 0 步探测发现沙箱里的脚本取不到数（没有令牌），给沙箱令牌就等于给了 actions 和 `/sim` 的权限；结果在沙箱里，不能作为记录被指令引用。第二版保留这种用法作为开放问题的参考计算，权威结果由证据插件产生。实验第 1 步以后不再做。
- **证据和风险检测做成 MCP 服务**（第二版第二稿）。持续检测改为 DSH 后台任务后，证据的使用方全在 DSH 里；做成插件能直接写会话工作区、交付文件、登记后台任务，少一个服务和一份令牌。代价是持续检测依赖 DSH 进程（重启后任务消失），以及确定性基线评测要用 Python 小工具按同一份采集清单取数（两份采集实现，用一致性测试约束）。
- **持续检测用子 Agent、DSH 定时任务或沙箱里的后台脚本。** 子 Agent 每次检测都花 token、是否按时取决于模型；定时任务按真实时钟、每次都跑一轮模型；沙箱脚本要给沙箱令牌、结果不能作为记录。见 `docs/design-v2.md` 4.9。
- **条文由 Agent 多次检索后拼接。** 条文要照抄进方案和台账，交给模型拼接会漏款、改字；完整条文是入库时的数据组织问题，按条建索引解决。

## 后果

- 契约升到 0.7.0：`Record`（取代 `Snapshot`）、`Origin`、`ResultCard`、`HumanInputRequest`、`ApprovalSummary`、`FlowEvent`；`snapshot_id` 改名 `record_id`。改动后 `make schema`，TS 侧按导出的 JSON Schema 校验。
- `fire_model`、`ventnet`、`dispatch`、`kb`、`workflow` 五个 MCP 服务和值守进程在 P4 后停用；Python 服务只剩模拟矿井和 actions。`make up` 的 7 个服务减为 2 个，DSH 以 Docker 部署。CLAUDE.md 的约定 1（数值只来自代码）、2（只有总指挥挂 actions、指令引用记录）、3（红线只实现一次）、4（智能体看不到真值）、5（mine-data 工具集合即适配器接口）继续有效，表述随之更新。
- 约定 1 的“代码”包括技能脚本：判据改动 = 改技能的 `references/` 或 `scripts/` + 自检样例，提交说明写明是占位值还是专家审定值。技能的发布流程（谁提交、谁评审）待定（`docs/design-v2.md` 第 10 节）。
- 插件代码量增加（证据、固定流程、知识库、会话入口），对 DSH 0.2.1-rc.4 插件 API 的依赖加深（`ctx.jobs`、`ctx.workflowEngine`、`ctx.workingDirectory`、`agent.followup`、`agent/error` 事件）。升级 DSH 时这些都要过验证清单。
- 评测：`grade.py` 改为读运行记录和证据记录判定；`snapshot_referenced` 改名 `record_referenced`；评测运行器要能按用例回答 `ask_user`。
- 实施按 `docs/design-v2.md` 第 9 节 P0–P6，在 `v2` 分支进行。
