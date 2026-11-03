# 真实 DSH 验证清单

骨架在无法访问 npm 的环境中搭建：TypeScript 插件只对照按 0.2.1-rc.4 签名编写的类型桩做过类型检查和单元测试，patch 只做过离线校验（`scripts/check_patches.py`）。**第一次在联网环境装好 DSH 后，按下表逐项验证**；以后每次升级 DSH 也重跑一遍。

验证人在“结果”列填 ✓ / ✗ 和日期；✗ 的项开 issue 并在此链接。

| # | 验证项 | 怎么验证 | 期望 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | TS 对真实包编译 | `cd dsh-bundle && pnpm install && pnpm typecheck && pnpm build`；提交 `pnpm-lock.yaml` | 无类型错误 | ✓ 2026-10-28：真实包（cordis 4.0.5 / dsh-tools 0.2.1-rc.4 / schemastery 3.18.5）下 typecheck、build 无错误，15 个 TS 测试通过；已提交 lock |
| 2 | bundle 安装 | `scripts/setup.sh` | `dsh plugin --profile rescue add ./dsh-bundle` 成功 | ✓ 2026-10-28：`dsh plugin --profile rescue list` 显示 `@rescue/dsh-bundle@link:../../../dsh-bundle`。pnpm 提示 link 方式不从配置档解析 peer 依赖（运行时只 import schemastery，待 #4/#9 观察）|
| 3 | patch 合成 | `dsh rescue --dump-config > var/config.yml` | 含 rescue-approval、rescue-audit、5 个 rescue-* 预设，默认 rescue-commander；`!!js` 原文正确（该命令输出未求值的原文，求值结果在 #5、#6 运行时核对） | ✓ 2026-10-28：rescue-approval、rescue-audit、5 个 rescue-* 预设在列，默认 rescue-commander，standard/ptc/minimal/cordis 均 `disabled: true`，`!!js` 原文与 patch 一致。原期望写“`!!js` 已求值成实际 URL”，与 `--dump-config` 实际行为不符，已改 |
| 4 | 关闭内置预设 | 网页端新建会话，查看预设列表 | 只有 rescue-*；默认 rescue-commander。若 `disabled` 行不生效，改用 registry 配置 | ✓ 2026-10-28：预设下拉只有 rescue-commander（默认）、灾情研判、物资调度、处置方案、合规审核；`disabled` 行生效，无需改 registry。当时新建会话失败（tool-todo 缺必填项 `allowParallelInProgress`），已修复并在 `check_patches.py` 增加插件配置校验 |
| 5 | 预设子配置里的 `!!js` | 改 `RESCUE_MCP_KB_URL` 后重启，看 kb 连接地址 | 使用新地址 | ✓ 2026-10-28：kb 改到 8115 端口、export `RESCUE_MCP_KB_URL=http://127.0.0.1:8115/mcp` 后重启 DSH（8105 无监听），合规审核 Agent 调 `get_article` 成功 |
| 6 | MCP 连接与令牌 | 设 `RESCUE_MCP_TOKEN`，重启服务和 DSH | 6 个服务均连接成功；不带令牌的 curl 返回 401 | ✓ 2026-10-28（修复后）：原因一是脚本加载 `.env` 时空值覆盖已 export 的令牌（改为 export 优先，`scripts/lib/env.sh`）；原因二是 `smoke.py`/`conformance.check_tools` 连接不带令牌（改用 `rescue_contract.client.connect`）。复测：6 个服务无令牌 401、带令牌 200，`make smoke` 带令牌全部通过；DSH 带令牌启动后 5 个预设合计挂载的 6 个服务工具均加载，指挥 Agent 实际调用 mine_data、fire_model、actions 成功 |
| 7 | 技能发现 | 各预设会话中询问“你有哪些技能” | 只看到 shared + 本角色目录下的技能 | ✓ 2026-10-28（修复后）：commander、dispatch 只有 shared；assessment、plan 各为本角色 2 个 + shared；compliance 原先缺 shared（`compliance.patch.yml` 漏写），已补上并在 `check_patches.py` 加检查，复测为 red-line-rules + report-templates。各预设 MCP 服务与配置一致，只有 commander 有 delegate |
| 8 | 委派 | 指挥 Agent：“请研判 1201 工作面” | 调用 delegate → rescue-assessment，结果返回指挥 Agent | ✓ 2026-10-28：指挥 Agent 调 delegate → rescue-assessment，研判结果返回指挥 Agent 汇总。前置修复：契约时间字段原为 `format: date-time` 但值不带时区，DSH 拒收 get_clock 等结果（改用 `MineTime`）；空列表结果 content 为空、模型看不到（`serving.ensure_text_content`）。观察：一次研判约 726K token；模拟时钟 1200 倍速，子 Agent 与指挥 Agent 取数相差约 12 模拟小时；研判 Agent 无 kb，不能引用条文 |
| 9 | 预检拦截 | 让指挥 Agent 下发缺少注惰步骤的封闭方案 | 不弹确认，直接拒绝，原因含“第85条”；审计记 denied | ✓ 2026-10-28：不弹确认，直接拒绝，原因含“[第85条]”；审计记 denied。模型默认会先送合规审核、拒绝直接提交，需在提示里说明这是验收测试才会直接调用 |
| 10 | 人工确认 | 下发合规的防灭火方案 | 弹出确认，显示确认人；确认后台账有记录、模拟器 actions 出现对应处置 | ✓ 2026-10-28 复验（修复 2772eb2 后，令牌 devtoken-6，s2 场景 60 倍速）：弹出“下发防灭火方案 requires confirmation by 总工程师”，只有 Reject / Allow once；Allow once 后台账记 `issue_firefighting_plan`、`forwarded_to_sim: true`，审计记 issued，模拟器 actions 出现 evacuate、leak_sealing、inert_injection（source=action），actions.log 无 401。过程：总指挥协调没有按指令直接提交，先交合规审核，判不通过后经人工选择“先修正再提交”，修订后提交；首次提交因多出 schema 外字段 `basis_note` 被预检按数据契约拦截（审计 denied），去掉后通过。首轮（修复前）：转发 `/sim/interventions` 不带令牌被 401，`forwarded_to_sim: false`。另：红线预检信任方案自填的 context，不与实测比对 |
| 11 | 拒绝确认 | 同上但点拒绝 | 指令未执行；审计记 denied（user rejected） | ✓ 2026-10-28：点 Reject 后指令未执行、台账无新记录；审计记 denied（detail 为 `the user rejected tool ...`，按消息正则识别） |
| 12 | 预检不可达 | 停掉 actions 后下发方案 | 拒绝，代码 RESCUE_PRECHECK_UNAVAILABLE | ✓ 2026-10-28：停 actions 后拒绝，原因“红线预检服务不可用，指令已拦截（fetch failed）”；DSH 会话日志 tool/result 的 error 为 `{name: RescueApproval, code: RESCUE_PRECHECK_UNAVAILABLE}`，模型看不到代码；审计记 denied（按 errorCode 识别），但审计记录本身不含 code 字段。注意：`scripts/mcp-down.sh` 忽略参数，会停掉全部服务 |
| 13 | danger-full-access 下仍拦截 | `DSH_PERMISSION_MODE=danger-full-access` 启动，重复 #9 | 仍被拒绝（预检不依赖审批策略）；记录 #10 在此模式下是否还弹确认 | ✓ 2026-10-28（export `DSH_PERMISSION_MODE=danger-full-access` 启动）：缺注惰步骤的封闭方案不弹确认、直接被拒（第85条，另报现场条件过时），审计 denied；`publish_warning` 仍弹“requires confirmation by 通风部门负责人”，只有 Reject / Allow once，Reject 后审计 denied、台账不变——因为 `cordis.patch.yml` 把审批策略写死为 ask。过程：总指挥协调第一次以“发布前必须过合规”为由拒绝跳过审核、未调用 actions，经合规审核后再要求提交才触发网关。副作用：此模式下沙箱为 danger-full-access、审批为 ask，不匹配任何权限预设，`@deepseek-ai/dsh-permission-presets` 启动失败（“composed sandbox and approval defaults match no preset”），网页端没有权限选择器 |
| 14 | 审计事件 | 检查 `var/audit/tool-actions.jsonl` | #9–#12 每次一行，状态正确 | ✓ 2026-10-28：#9–#12 共 5 次调用各一行，状态依次 denied / issued / issued / denied / denied，与实际一致 |
| 15 | 上下文压缩 | s3 场景连续研判 2 小时（加速） | 不超上下文；压缩后仍记得已下发的指令 | ✓（压缩为手动触发）2026-10-28：s3、18 倍速，真实约 1 小时 50 分 = 模拟 0→31.8 h，总指挥协调同一会话 12 轮研判（每轮委派 rescue-assessment，部分轮次加 rescue-compliance，共 14 个子 Agent）。第 3 轮经人工确认下发 publish_warning（A-4f00d9e691，none）；第 5 轮起按指示不再提交。累计约 10M token（缓存命中 96–99%），上下文占用由 2% 线性涨到 19%，2 小时内**没有触发自动压缩**，未超上下文。随后手动 `/compact`：“Compacted 301 history items (~90990 tokens)”，占用 19%→10%；压缩后不调工具追问已下发指令，答出唯一一条 publish_warning、等级 none、回执 A-4f00d9e691、时间 16:34:03，与台账一致，并正确列出被合规判不通过和未提交的指令。注意：网页端发出 `/compact` 后页面立即断开会导致压缩被中止（“This operation was aborted”），需保持页面打开到完成。自动压缩在更长会话中的触发与效果仍未验证 |
| 16 | 本地大模型 | `scripts/dev.sh --local-llm`（有可用端点时） | 能选中 rescue-local 模型并完成 #8 | ✓ 2026-10-29（qwen35-122b-a10b，OpenAI 兼容网关）：模型下拉出现 rescue-local 分组并可选中；指挥 Agent 调 delegate → rescue-assessment，两者都走 rescue-local，结果返回指挥 Agent 汇总；约 1 分钟、67K token，未调用 actions。`scripts/dev.sh --local-llm` 原先启动失败（`unknown option '--patch'`），已修复并复验，见下方“#16 本地大模型”。研判内容质量问题也见下方 |
| 17 | `.env` 加载 | 不 export、只写 `.env` 时启动 | 确认 DSH_* 必须 export 的说法；如有变化更新 `scripts/dev.sh` | ✓ 2026-10-28：`DSH_HOME` 只写在 `.env` 时 DSH 找不到 rescue 配置档；export 后正常。`scripts/dev.sh` 无需改 |

| 18 | DSH 自带工作流（可选能力，评估是否采用） | 叠加 patch 让 rescue-commander 挂载 `dsh-workflow-ptc` + `dsh-tool-workflow`（见下方“#18”），经网页端让它用 `workflow` 工具运行固定脚本：子 Agent 报告工具和人设、调 fire_model、调 kb、借 delegate 转 rescue-compliance、提交 publish_warning | 记录能否运行、子 Agent 用哪个预设、结构化输出是否可靠、人工确认是否照常 | ✓ 2026-11-02（评估完成，**不采用**，见 ADR 0006）：workspace-write 下能运行；子 Agent 只能用调用方自己的预设（有 actions、没有 kb），不能再委派；结构化输出 5/5 符合 schema；子 Agent 调 actions 时审批被强制为 never，未弹确认即被拒，审计记成“用户拒绝”。详见下方“#18” |
| 19 | 插件工具编排（确定性工具调用 + 指定预设的子 Agent） | 验证分支 `spike/plugin-workflow`：验证插件 `dsh-bundle/src/spike` 注册工具 `spike_plan_review`，叠加 patch `dsh-bundle/spike/plan-review.patch.yml` 挂到 rescue-commander；评测用例 `spike-plan-review`（s4 第 60 小时）让总指挥调用它（见下方“#19”） | 插件里能调 MCP 工具、能按预设起子 Agent 并拿到结构化结果、审批和审计照常、网页端能显示运行过程 | ✓ 2026-11-02：四项都成立；子 Agent 照抄整份方案作工具参数时 DeepSeek 偶发生成坏 JSON，改为插件做红线检查后 3/3 正常。详见下方“#19” |
| 20 | 插件登记后台任务（持续检测 → 唤醒 → 询问操作员） | 验证插件 `dsh-bundle/src/spike-watch` 注册工具 `spike_watch`（once / watch），watch 用 `ctx.jobs.start` 登记后台任务；叠加 patch `experiments/spike20/watch.patch.yml`（由 `gen_patch.py` 生成）给 rescue-commander 加 `dsh-tool-jobs` 和 persona 规则；`experiments/spike20/drive.py` 经网页端提问、推进模拟时钟、读会话日志（见下方“#20”） | 插件能登记任务并立即返回；网页端任务列表显示进度、可停止；任务结束时唤醒空闲会话（含网页已关闭、长时间空闲）；唤醒后能读结果、弹出提问并按回答处理；停止、重启时的行为明确 | ✓ 2026-11-03（机制可行，有一处要补救）：登记、进度、两种停止、唤醒、提问（三个选项 + 自由回答）、网页关闭后唤醒和补答都成立；空闲 20 分钟后任务仍在、唤醒正常，但唤醒的这一轮因 DeepSeek 接口错误中断，没有重试，异常没有送到人。DSH 重启或崩溃后任务消失、不通知。详见下方“#20” |
| 21 | 容器内的 bash 沙箱 | 第二版 P0：DSH 镜像里开 `dsh-bash-sandbox`，复跑实验 0001 第 0 步探测（`exp-probe-env`） | 沙箱在容器里能工作；工作区、技能目录、记录目录的读写权限与本机一致 | 未验证 |
| 22 | 插件起 Python 子进程（证据插件的 `runScript`） | 验证分支 `spike/plugin-subprocess`：验证插件 `dsh-bundle/src/spike-script` 的 `spike_run_script` 在 DSH 进程里起子进程运行技能脚本替身 `experiments/spike22/script.py`，`spike_export` 把记录写进会话工作区；叠加 patch `experiments/spike22/spike.patch.yml`，`drive.py script` 经网页端驱动（见下方“#22”） | 子进程不受 bash 沙箱限制、能写记录目录；超时、非零退出、输出不合格能分类报告；沙箱写不了记录目录；导出的记录能在沙箱里读 | ✓ 2026-11-03：都成立。另有两点要进设计：子进程默认继承 DSH 进程的全部环境变量（含模型、RAGFlow、MCP 的密钥），必须按白名单传；子进程没有任何限制（能写技能目录、能出网），脚本只能是评审过的代码。详见下方“#22” |
| 23 | 唤醒失败的补救（#20 遗留） | 同一验证分支：`spike_job` 登记后台任务，插件盯住被唤醒的这一轮，以 `agent/error` 结束时补发通知；故障注入代理 `experiments/spike22/fault_proxy.py` 把唤醒这一轮的模型流截断在 `message_stop` 之前；`drive.py wake N`（见下方“#23”） | 复现 #20 的故障；插件补发后智能体能提问；或者 provider 重试能在同一轮里恢复 | ✓ 2026-11-03：复现了 #20（`STREAM_CLOSED` 不在默认可重试列表里，这一轮以 error 结束）。插件补发：截断 1 次后第 2 轮正常提问；连续截断 4 次时补发 3 次后停止。provider 重试：把 `STREAM_CLOSED` 加进 `retryPolicy.retryableCodes` 后同一轮重试 2 次恢复。两者都做。详见下方“#23” |

## 已知需要关注的点

- `rescue-approval` 在 `tools/pre-execute` 里先调 `next()` 再决定 ask：若上游已 deny/cancel，保持上游结论。需确认 rc.4 的瀑布顺序与预期一致（#9、#10）。
  - 2026-10-28 rc.4 实测：违规方案直接 deny 不弹确认（#9），合规方案弹确认（#10），与预期一致。
- `rescue-audit` 根据错误码和错误信息正则判断 issued / denied / failed（`src/audit/record.ts`）。DSH 改错误文案会导致误判，#11、#12 要核对。
  - 2026-10-28 rc.4 实测：插件 deny 的 `info.code` 能传到审计（#9、#12 按 code 识别）；用户拒绝没有 code，靠 `the user rejected tool` 文案识别（#11）。
- 确认人按角色限定暂未实现（DSH 侧需要账号/角色能力）。
- 权限预设与人工确认（2026-10-28 rc.4 实测）：
  - export `DSH_PERMISSION_MODE=danger-full-access` 时，`cordis.patch.yml` 把审批写死为 ask，确认照常弹出（#13）；但沙箱 full-access + 审批 ask 不匹配任何预设，`dsh-permission-presets` 启动失败，网页端没有权限选择器。
  - 正常启动时，网页端输入框可选 Read Only / Workspace Write / Full access。选 Full access（确认“Enable Full access?”）后审批策略变为 never，actions 的指令**一律被自动拒绝**，不弹确认、不会放行；审计记为 denied，detail 是 `the user rejected tool ...`，与真人拒绝无法区分（`var/audit/tool-actions.jsonl` 2026-10-29T02:32:07Z 一条）。不会绕过人工确认，但会让总指挥协调无法发布任何指令，且复盘时会误以为是人拒绝的。
  - 已处理（2026-10-28）：`cordis.patch.yml` 覆盖 `permission` 的预设表，只保留 Read Only / Workspace Write（审批均为 ask），并显式 `defaultPreset: workspace-write`；`check_patches.py` 要求所有权限预设审批为 ask。复验：正常启动和 export `DSH_PERMISSION_MODE=danger-full-access` 启动都没有插件启动失败，选择器只有 Read Only / Workspace Write。代价：环境变量模式下沙箱也是 workspace-write（救援预设没有 bash 和文件工具，无实际影响）。
- #15 长时间研判中发现（2026-10-28，s3）：
  - 乙烯/乙炔真实为 0（s3 高温点 90 ℃ 以上才出现）时，`fire_model` 的 `detect_stuck`（最近 6 点完全不变即判卡值）把它们判为“疑似卡值、已排除”，指标为 null；Agent 连续 10 轮报告“标志气体判据缺失、无法判定”，而不是“未检出”。已修复（6b2660e）：恒为 0 且同测点 CO 仍在变化时按未检出处理，整根束管卡住时仍排除。
  - 第 10 轮起 `check_realtime_alarms` 报第164条红线（束管 CO 上升速率超限），同时 `assess_warning_level` 仍为 none（第 11 轮才到 blue）；红线报警也没有进入 `get_events`。两套判据不一致，Agent 每轮都在提示这个矛盾。已处理：根因是第164条复燃判据对从未封闭的 s3 也生效。契约 0.2.0 新增 `mine_data.get_fire_areas`（火区台账），第164条只对台账中已封闭或已启封的火区检查；`assess_warning_level` 同时返回 `realtime_alarms`，等级与报警仍独立判定；`get_events` 说明改为只含监控系统自身事件。
- #16 本地大模型（2026-10-29，qwen35-122b-a10b，s2）：
  - `scripts/dev.sh --local-llm` 报 `error: unknown option '--patch'`：`--patch` 是 dsh 启动器参数，必须写在 app 参数 `--no-open` 之前，而脚本拼成了 `dsh rescue --no-open --patch ...`。已修复：脚本改为 `dsh rescue --patch ... --no-open`；复验 `scripts/dev.sh --local-llm` 正常启动，模型下拉有 qwen35-122b-a10b。
  - 该网关没有 `/models` 接口（`.env` 说明里“从 /v1/models 查模型 id”不适用），但 chat completions、流式输出和工具调用都正常，DSH 不依赖 `/models`。
  - 本机代理环境变量 `ALL_PROXY=socks://...` 会让 MCP 客户端（httpx）报 `Unknown scheme for proxy URL`，`make smoke` 全部失败；`NO_PROXY` 的 `127.*` 通配写法 httpx 也不认。本次 unset 代理变量后启动。属本机环境问题，代码不处理：运行 MCP 服务和 DSH 的环境不要设置 socks 代理。
  - 研判内容比 DeepSeek 弱，违反“数值只来自代码”的约定：研判 Agent 自行写出“氧浓度降至 8% 以下”“CO 上升速率超过 1.0 ppm/h 立即启动应急响应”“束管加密至 30 分钟/次”“采空区氧浓度已降至安全水平”，均无工具依据（报告里的“>100℃ 才出现乙烯”来自技能 `fire-warning-assessment` 原文，不是模型编的）；声称依据含“ventnet 通风网络分析”，实际未调用 ventnet；把指挥 Agent 转述的旧读数（G1/G2 氮气 85.3%/88.9%）与自己新取的计算结果（G2 92.0%）混在同一时刻下报告。指挥 Agent 委派 18 秒后就发消息催“请加快研判进度”。弱模型上线前需要输出侧约束（例如报告中的数值必须能在本轮工具结果里找到）和智能体端到端评测。
  - 智能体端到端评测（2026-10-29，`eval/agent`，s2 第 48 小时“请研判 1201 工作面”）：qwen 在 5 次有效运行中只有 1 次全部通过，多次不委派研判 Agent、编造升级预警的阈值（如“温度上升速率超过 0.5℃/h 建议升级”“60℃以上采取注浆”）、点名未调用的 ventnet；另有 1 次本地端点连接失败。**结论：该本地模型不适合担任救援 Agent，不再纳入评测。** 换用其他本地模型时，先用 `make eval-agent` 与 DeepSeek 对比再决定。
- #18 DSH 自带工作流（2026-11-02，s4 第 55 小时，deepseek-official/deepseek-flash，两轮共 5 个子 Agent）：
  - 是什么：`dsh-workflow`（引擎接口）+ `dsh-workflow-ptc`（在 Node PTC 沙箱里执行）+ `dsh-tool-workflow`（给模型的 `workflow` 工具）。模型现写一段 JavaScript，脚本只能用 `agent()`、`parallel()`、`pipeline()`、`phase()`、`log()`；**不能直接调用 MCP 工具**，每一步都是一个大模型子 Agent。rescue 配置档里这几行默认 `disabled: true`。
  - 试验方法：不入库的叠加 patch，按 id 覆盖 `preset-rescue-commander`（内容同 `presets/commander.patch.yml`），在 plugins 末尾加：
    ```yaml
    - id: workflow-trial
      name: cordis:group
      group: true
      isolate:
        workflowEngine: true
      config:
        - id: workflow-ptc
          name: '@deepseek-ai/dsh-workflow-ptc'
          config:
            provider: spawn
        - id: tool-workflow
          name: '@deepseek-ai/dsh-tool-workflow'
          config:
            enableRunInBackground: false
    ```
    用 `dsh rescue --patch <该文件> --no-open` 启动（台账、审计按 `make dev-eval` 写到 `var/eval/`）。
  - 运行：workspace-write 权限下正常运行（本机有 `/usr/bin/bwrap`；PTC 文档写明没有沙箱隔离时 read-only、workspace-write 会直接失败，换机器要先确认）。总指挥协调原样提交了脚本。
  - 子 Agent 的预设：`agent()` 的请求只有 prompt、schema、provider、model（`dsh-workflow-ptc` 源码 `startChild` → `subagents.startActivation`），**不能指定预设**。子 Agent 实际用的是调用方的 rescue-commander：人设相同，工具含 mine_data、fire_model、**actions**、delegate、workflow，**没有 kb**。
  - 借道委派：子 Agent 调 delegate 被拒，原因是委派层数上限（当前深度 1，上限 1）。所以子 Agent 也拿不到 kb 和其他 rescue-* 预设。
  - 结构化输出：5 个子 Agent 都按 schema 返回；取预警等级的子 Agent 照抄 `level=red`、`stage_name=加速氧化`，与工具结果一致；每个子 Agent 6–10 秒。
  - 人工确认：子会话日志里 `approval/policy` 为 `{"policy": "never", "source": "delegation"}`。子 Agent 调 `publish_warning` 时红线预检通过、记了 `approval/asked`，随即 `approval/decided: rejected`，网页端没有弹确认；`var/eval/audit/tool-actions.jsonl` 2026-11-02T16:24:15Z 一条 denied，detail 为 `the user rejected tool ...`，与真人拒绝无法区分（与“Full access 下指令一律被自动拒绝”同类）。指令发不出去，不会绕过确认，但复盘时会误判为人拒绝。
  - 结论：不采用（ADR 0006）。确定性流程改为 MCP 工具实现（`docs/workflow-design.md`）。以后 DSH 版本若支持 `agent()` 指定预设，再重新评估。
  - 更正（2026-11-02，见 #19）：`workflow` 工具的参数就是脚本，可以要求模型原样提交固定脚本，并不是“每次现写”；DSH 没有“保存的工作流”，但插件可以用 `ctx.workflowEngine.start({ script, meta, args, parent })` 运行仓库里审定过的脚本。“`agent()` 不能指定预设”的原因是 `dsh-workflow-ptc` 的 `startChild` 只传 prompt、schema、provider、model，没有把 `agentPreset` 传给 `subagents.startActivation`（后者支持）。
- #19 插件工具编排（2026-11-02，s4 第 60 小时，deepseek-official/deepseek-flash，验证分支 `spike/plugin-workflow`）：
  - 做法：验证插件在工具 `spike_plan_review` 里依次 ① 用 `ctx.tools.execute` 调 `mcp__workflow__draft_plan`、`mcp__mine_data__get_clock` 和总指挥没有挂载的 `mcp__kb__check_plan_redlines`；② 直接 POST actions 的 `/redlines/check` 做红线检查；③ 用 `ctx.subagents.startActivation({ provider: 'spawn', delivery: 'caller', request: { agentPreset: 'rescue-compliance', outputSchema, parent, prompt } })` 起合规审核子 Agent；④ 往调用方会话写 `tool-workflow/run-start`、`agent-start`、`agent-end`、`run-end`；⑤ 经 `ctx.tools.execute` 调 `mcp__actions__issue_sealing_plan`（评测运行器在确认弹窗点拒绝）。
  - 插件调 MCP 工具：正常，结果是 `{ content, structuredContent }`，draft_plan 约 0.75 秒。带不带 `parent`（调用方的 `exec.token`）都能调。只能调调用方 Agent 范围内的工具：总指挥调 `mcp__kb__check_plan_redlines` 返回 `UNKNOWN_TOOL`。
  - 审批与审计：经插件提交的 actions 调用照常走 `tools/pre-execute`：rescue-approval 预检通过后在总指挥会话记 `approval/asked`（reason 含人工确认项），网页端弹确认，拒绝后记 `approval/decided: rejected`；rescue-audit 记入 `var/eval/audit/tool-actions.jsonl`，call_id 为 `<外层 callId>:spike:4`。
  - 会话日志：插件内部的工具调用**不写** `tool/call`、`tool/result`，总指挥会话只有外层 `spike_plan_review` 一条。`grade.py` 的 tools_called 等检查看不到这些步骤；正式实现要自己记录步骤（写快照或会话事件）。
  - 指定预设的子 Agent：子会话 `agent-preset/selected: rescue-compliance`，工具为 kb 全套 + skill + structured_output，没有 actions；`approval/policy: never (delegation)`，与 delegate 相同；父会话为总指挥，层数与 delegate 委派的子 Agent 相同。结构化输出（`outputSchema`）走 `structured_output` 工具，正常结束的 4 次都按 schema 返回。
  - 坏 JSON：先让子 Agent 自己调 `check_plan_redlines`（要把整份方案 JSON 抄进工具参数），4 次中 3 次子 Agent 以 `MALFORMED_RESPONSE: DeepSeek Messages stream: tool input is invalid JSON` 结束（与 `s4-sealing-plan` 的“接口错误”同类）。改为插件直接调 `/redlines/check`、把结论和方案放进提示词、要求子 Agent 不调 `check_plan_redlines` 之后，3/3 正常结束，子 Agent 都没有调它；引用的条文全部出现在本次 kb 检索结果里。单次 87–117 秒，总指挥约 4 万 token（主要是系统提示词），合规子 Agent 2.4–5.7 万 token。
  - 网页端：`dsh-client-ui-workflow-run` 认插件写的 `tool-workflow/*` 事件，工具调用下面显示运行节点（“spike-plan-review · 1 member”），展开为阶段“合规审核”和成员“合规审核（rescue-compliance）Completed”。只显示子 Agent 成员，确定性步骤和 `phase()`/`log()` 不显示。
  - 其他：工具返回值必须是无损 JSON，含值为 `undefined` 的字段会被拒（`INVALID_TOOL_OUTPUT: value is not lossless JSON`）。插件改动后要重启 DSH。
  - 结论：“插件工具按代码顺序编排确定性工具调用和指定预设的子 Agent，指令仍由总指挥提交、人工确认”在 rc.4 上可行。红线检查这类确定性步骤由插件直接做，子 Agent 不要抄写大段 JSON。
- #20 插件登记后台任务（2026-11-03，s4 第 15→25 小时，等级 none → blue，deepseek-official/deepseek-flash）：
  - 做法：验证插件 `dsh-bundle/src/spike-watch` 注册工具 `spike_watch`。`once` 检测一次；`watch` 先检测一次作为基线，再用 `ctx.jobs.start({ kind: 'rescue-watch', owner: exec.agent.id, toolCall, run })` 登记后台任务。任务在 DSH 进程里每 2 秒读一次矿井时钟，每过 10 矿井分钟检测一次，等级高于基线就以 `completed`（detail `anomaly`，result 为 JSON）结束。检测借用 workflow 的 `assess_panel`（正式实现换成 `check.py`）。插件自带最小 MCP 客户端（streamable-http，fetch 实现），直接连 mine_data 和 workflow，不经模型的工具范围。叠加 patch 给总指挥加 `dsh-tool-jobs`（`maxConsecutiveWakes: 3`）和 persona 规则：once 照抄回答；watch 返回任务编号后结束本轮、不轮询；收到完成通知后 `job_output`，anomaly 为真时用 `ask_user_question` 问“开始完整研判 / 继续检测 / 暂不处理”，也可自由回答。模拟矿井用手动时钟，`drive.py` 推进。DSH 用单独的 `DSH_HOME`（`.dsh-home/spike20-home`）和 3081 端口。
  - 前提：rescue 配置档本来就有全局的 `dsh-jobs-local`；`dsh-tool-jobs` 按预设挂载（正式预设里没有，必须加，否则 `ctx.jobs.start` 因没有控制器而失败）。
  - 登记：工具 0.5 秒返回任务编号，会话日志记 `job/registered`（含 owner、label、创建它的 toolCall）。智能体照规则说明“已开始持续检测”后结束本轮，没有轮询。插件自带的 MCP 客户端正常，`get_clock`、`assess_panel` 每次不到 1 秒。
  - 进度与停止（网页端）：标题栏显示“1 background job running”，展开后有任务行、`updateProgress` 写的进度行（“基线 none；等待矿井时间推进”）和停止按钮。停止是**两击确认**（首击武装，3 秒内再点才调 `ctx.jobs.kill`）。网页停止后任务以 `killed, cancelled by the user` 结束，**照常通知并唤醒**智能体。
  - 唤醒：推进到异常后 2 秒内任务结束（`job/settled`），同一秒 `tool-jobs` 投递“background job … finished [status: completed, anomaly]”并开新一轮。智能体 `job_output` 读到检测记录和 result，随即 `ask_user_question`：三个选项（模型给第一项加了“（推荐）”，网页显示为 Recommended 标记）和自带的“Type your answer”输入框。从结束到弹出提问 11–26 秒（含一次模型重试）。
  - 回答：选“开始完整研判”→ 调 `assess_panel` 并按研判规则回答；自由回答“先不研判，把 1201 上隅角最近的 CO 读数告诉我”→ 不研判，按要求查读数；选“暂不处理”→ 确认结束，并提醒已没有持续检测在运行。
  - 网页关闭：关闭浏览器后推进到异常，唤醒、`job_output`、提问照常发生（不依赖有人开着网页）。重新打开后会话列表里该会话有橙点，悬停显示“Waiting for answer”；点开后提问卡片还在，约 67 秒后作答正常送达。当前 `ask_user_question` 是阻塞模式（legacy），会一直等人回答。
  - 空闲 20 分钟（网页关闭）：任务一直在运行，推进到异常后正常结束并开新一轮——Agent 没有因空闲被释放。**但这一轮的模型请求卡了 178 秒，以 `DeepSeek Messages stream ended before message_stop` 结束，没有重试，轮次以 error 结束，没有提问**：异常已检测到、任务已结束，却没有送到人。同一时段 DeepSeek 接口明显过载（单步 80–275 秒，之后还出现 “Service is too busy”），判断为模型服务的问题，不是空闲造成的；未在接口稳定时复测。
  - 重复启动：同一会话再要求“再启动一次持续检测”，智能体先 `job_list` 发现已有任务在运行，用提问请示是否重启，没有重复启动（插件的“一会话一个任务”检查没有被触发到）。回答“停掉”后调 `job_kill`。
  - 模型停止：`job_kill` 立即结束（`killed`，cause `kill`，插件的取消处理正常），之后推进到异常没有任何唤醒（模型自己停的任务不再通知，符合 `dsh-tool-jobs` 文档）。插件把停止原因又写进 detail，与 DSH 自动附加的重复，正式实现不要写。
  - DSH 正常关闭：会话日志记 `job/settled`（`killed`，cause `teardown`，detail `jobs service disposed`），不通知。重启后任务不存在，推进到异常没有任何反应；问“持续检测还在运行吗”时，智能体用 `job_list`/`job_output` 发现任务已停，提问是否重启。
  - DSH 崩溃（`kill -9`）：会话日志只有 `job/registered`，没有 `job/settled`。
  - 没有验证：`maxConsecutiveWakes` 的效果；超过 20 分钟的空闲；同时多个会话各有任务。
  - 结论：“插件登记后台任务、不经模型持续检测、结束时由 DSH 唤醒智能体询问操作员”在 rc.4 上可行，网页关闭也不影响。要补两处：① **唤醒的这一轮失败时异常会丢**（模型接口错误不重试），需要兜底（见设计文档 4.9）；② DSH 重启、崩溃后任务消失且不通知，以后无人值守的恢复服务可以按会话日志判断——有 `job/registered` 而没有 `job/settled`（崩溃），或 `job/settled` 的 cause 为 `teardown`（关闭）。
- #22 插件起 Python 子进程（2026-11-03，deepseek-official/deepseek-flash，验证分支 `spike/plugin-subprocess`，不需要 MCP 服务）：
  - 做法：插件用 `node:child_process.spawn` 运行 `python3 -I script.py --case <case> --input-file … --output-file …`，cwd 是记录目录旁的运行目录，环境变量只传 `PATH`、`LANG`（另加 `PYTHONIOENCODING`），超时 5 秒（占位）先 SIGTERM、1 秒后 SIGKILL。模型在一个会话里按顺序调 7 种 case，再导出 ok 的记录，用 bash 读它、尝试写记录目录。
  - 分类：ok（约 35–40 毫秒，含 Python 启动）、timeout（5009 毫秒后终止）、exit（退出码 3，带 stderr）、invalid-output（输出不是 JSON）、schema（缺 `result`）都按预期报告；ok 时记录原子写入，带脚本、输入、输出的 sha256。
  - 子进程不受 bash 沙箱限制：bash 沙箱只套在 bash 工具上（`dsh-bash-sandbox` 替换 `ctx.shell`），插件自己起的子进程以 DSH 进程的用户运行，能写记录目录、**也能写 `skills/`**、能看到全部 469 个进程、能连外网。脚本因此只能是评审过的代码；“脚本不访问网络”（设计 4.2 脚本约定 1）没有机制保证，靠评审和容器（技能目录只读挂载、出站白名单）。
  - 环境变量：按白名单传时子进程只有 `LANG`、`PATH`、`PYTHONIOENCODING`。`probe-inherit`（故意继承）时能看到 `DEEPSEEK_API_KEY`、`RAGFLOW_API_KEY`、`RESCUE_MCP_TOKEN`、`RESCUE_LLM_API_KEY`、`DSH_HOME` 等全部变量。**`runScript` 必须按白名单传环境变量**（DSH 自带的 `ctx.subprocess` 用 `scrubbedParentEnv()` 去掉名字像凭据的变量和 `DSH_*`，也可以用它，但白名单更严）。
  - 沙箱：bash 写记录目录里的新文件、覆盖已有记录都返回 `Read-only file system`，并提示可以申请提权重试（模型按 persona 没有申请）；写工作区成功。导出的记录模型能用 bash `cat` 读出。
  - 工作区：`ctx.workingDirectory.ensure(agent)` 返回 `~/Documents/deepseek-harness/default-workspace`，**所有会话共用这一个目录**。`exportRecord` 要按会话或记录分子目录，不能假定工作区只属于一个会话。
  - 结论：设计 4.3 的 `runScript`（插件在 DSH 进程里起子进程、结果写沙箱写不了的记录目录）可行；要加环境变量白名单、导出按会话分目录，并在设计里写明子进程不受限制。
- #23 唤醒失败的补救（2026-11-03，同上）：
  - 故障注入：`DEEPSEEK_BASE_URL` 指向本地代理，代理转发到 `api.deepseek.com`，装填后把流在前 4 个 SSE 事件后正常结束（不发 `message_stop`）。DSH 报 `DeepSeek Messages stream ended before message_stop`（错误码 `STREAM_CLOSED`），与 #20 空闲测试里的错误相同。（代理到上游偶发 TLS 握手失败，代理内部重试，不算注入的故障；DSH 对 `TRANSPORT` 默认会重试。）
  - DSH 默认：`STREAM_CLOSED` 不在默认可重试列表（`EMPTY_RESPONSE`、`RATE_LIMIT`、`SERVER`、`TIMEOUT`、`TRANSPORT`）里，唤醒这一轮直接以 `turn/end`（error）结束，复现了 #20。
  - 插件补发：插件监听 `agent/error` 和 `agent/status`，被唤醒的这一轮出错后回到 idle 时，用 `agent.followup()` 补发一条通知（来源沿用 `tool-jobs` 的 notice）。截断 1 次：第 2 轮读 `job_output`、弹出提问、回答后正常结束。截断 4 次：补发 3 次后停止，记 `wake-gave-up`。补发是立即的，没有退避，真实故障时 3 次会在约 30 秒内用完。
  - provider 重试：叠加 patch 给 `llm-deepseek` 配 `retryPolicy: { mode: normal, maxRetries: 3, retryableCodes: [默认 5 个, STREAM_CLOSED] }`，关掉插件补发。截断 2 次：同一轮里 `llm/retry` 2 次后恢复，正常提问，没有多出轮次。重试发生在模型输出完整之前，工具还没执行，不会重复执行工具。
  - 结论：两者都做。provider 重试是第一道（对所有轮次都有效，不只唤醒）；插件补发是第二道，覆盖重试用完或不可重试的错误，正式实现要加退避（占位 1、5、15 分钟）；都失败时由演示控制台显示“未确认的异常”。没有验证：真实接口长时间卡住（#20 中 178 秒）时重试的表现；补发与操作员同时输入的交错。
