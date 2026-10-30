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
  - 研判内容比 DeepSeek 弱，违反“数值只来自代码”的约定：研判 Agent 自行写出“>100℃ 才出现乙烯”“氧浓度降至 8% 以下”“CO 上升速率超过 1.0 ppm/h 立即启动应急响应”“束管加密至 30 分钟/次”“采空区氧浓度已降至安全水平”，均无工具依据；声称依据含“ventnet 通风网络分析”，实际未调用 ventnet；把指挥 Agent 转述的旧读数（G1/G2 氮气 85.3%/88.9%）与自己新取的计算结果（G2 92.0%）混在同一时刻下报告。指挥 Agent 委派 18 秒后就发消息催“请加快研判进度”。弱模型上线前需要输出侧约束（例如报告中的数值必须能在本轮工具结果里找到）和智能体端到端评测。
