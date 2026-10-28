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
| 10 | 人工确认 | 下发合规的防灭火方案 | 弹出确认，显示确认人；确认后台账有记录、模拟器 actions 出现对应处置 | ✗ 2026-10-28：弹出确认并显示确认人（publish_warning→通风部门负责人，issue_firefighting_plan→总工程师），只有 Reject / Allow once；确认后台账有记录、审计记 issued。但模拟器 actions 为空：actions 转发 `/sim/interventions` 不带令牌，401（`var/log/actions.log`：转发模拟处置失败：HTTP Error 401），台账 `forwarded_to_sim: false`。另：红线预检信任方案自填的 context，不与实测比对 |
| 11 | 拒绝确认 | 同上但点拒绝 | 指令未执行；审计记 denied（user rejected） | ✓ 2026-10-28：点 Reject 后指令未执行、台账无新记录；审计记 denied（detail 为 `the user rejected tool ...`，按消息正则识别） |
| 12 | 预检不可达 | 停掉 actions 后下发方案 | 拒绝，代码 RESCUE_PRECHECK_UNAVAILABLE | ✓ 2026-10-28：停 actions 后拒绝，原因“红线预检服务不可用，指令已拦截（fetch failed）”；DSH 会话日志 tool/result 的 error 为 `{name: RescueApproval, code: RESCUE_PRECHECK_UNAVAILABLE}`，模型看不到代码；审计记 denied（按 errorCode 识别），但审计记录本身不含 code 字段。注意：`scripts/mcp-down.sh` 忽略参数，会停掉全部服务 |
| 13 | danger-full-access 下仍拦截 | `DSH_PERMISSION_MODE=danger-full-access` 启动，重复 #9 | 仍被拒绝（预检不依赖审批策略）；记录 #10 在此模式下是否还弹确认 | 未验证 2026-10-28：以 `DSH_PERMISSION_MODE=danger-full-access` 启动被执行环境的权限策略拦截，需人工执行 |
| 14 | 审计事件 | 检查 `var/audit/tool-actions.jsonl` | #9–#12 每次一行，状态正确 | ✓ 2026-10-28：#9–#12 共 5 次调用各一行，状态依次 denied / issued / issued / denied / denied，与实际一致 |
| 15 | 上下文压缩 | s3 场景连续研判 2 小时（加速） | 不超上下文；压缩后仍记得已下发的指令 | 未验证：耗 token 较多（#8 一次研判约 726K），待确认后再跑 |
| 16 | 本地大模型 | `scripts/dev.sh --local-llm`（有可用端点时） | 能选中 rescue-local 模型并完成 #8 | 未验证：无可用本地大模型端点（`.env` 中仍为占位地址） |
| 17 | `.env` 加载 | 不 export、只写 `.env` 时启动 | 确认 DSH_* 必须 export 的说法；如有变化更新 `scripts/dev.sh` | ✓ 2026-10-28：`DSH_HOME` 只写在 `.env` 时 DSH 找不到 rescue 配置档；export 后正常。`scripts/dev.sh` 无需改 |

## 已知需要关注的点

- `rescue-approval` 在 `tools/pre-execute` 里先调 `next()` 再决定 ask：若上游已 deny/cancel，保持上游结论。需确认 rc.4 的瀑布顺序与预期一致（#9、#10）。
  - 2026-10-28 rc.4 实测：违规方案直接 deny 不弹确认（#9），合规方案弹确认（#10），与预期一致。
- `rescue-audit` 根据错误码和错误信息正则判断 issued / denied / failed（`src/audit/record.ts`）。DSH 改错误文案会导致误判，#11、#12 要核对。
  - 2026-10-28 rc.4 实测：插件 deny 的 `info.code` 能传到审计（#9、#12 按 code 识别）；用户拒绝没有 code，靠 `the user rejected tool` 文案识别（#11）。
- 确认人按角色限定暂未实现（DSH 侧需要账号/角色能力）。
