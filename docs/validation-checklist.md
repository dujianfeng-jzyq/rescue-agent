# 真实 DSH 验证清单

骨架在无法访问 npm 的环境中搭建：TypeScript 插件只对照按 0.2.1-rc.4 签名编写的类型桩做过类型检查和单元测试，patch 只做过离线校验（`scripts/check_patches.py`）。**第一次在联网环境装好 DSH 后，按下表逐项验证**；以后每次升级 DSH 也重跑一遍。

验证人在“结果”列填 ✓ / ✗ 和日期；✗ 的项开 issue 并在此链接。

| # | 验证项 | 怎么验证 | 期望 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | TS 对真实包编译 | `cd dsh-bundle && pnpm install && pnpm typecheck && pnpm build`；提交 `pnpm-lock.yaml` | 无类型错误 | |
| 2 | bundle 安装 | `scripts/setup.sh` | `dsh plugin --profile rescue add ./dsh-bundle` 成功 | |
| 3 | patch 合成 | `dsh rescue --dump-config > var/config.yml` | 含 rescue-approval、rescue-audit、5 个 rescue-* 预设；`!!js` 已求值成实际 URL | |
| 4 | 关闭内置预设 | 网页端新建会话，查看预设列表 | 只有 rescue-*；默认 rescue-commander。若 `disabled` 行不生效，改用 registry 配置 | |
| 5 | 预设子配置里的 `!!js` | 改 `RESCUE_MCP_KB_URL` 后重启，看 kb 连接地址 | 使用新地址 | |
| 6 | MCP 连接与令牌 | 设 `RESCUE_MCP_TOKEN`，重启服务和 DSH | 6 个服务均连接成功；不带令牌的 curl 返回 401 | |
| 7 | 技能发现 | 各预设会话中询问“你有哪些技能” | 只看到 shared + 本角色目录下的技能 | |
| 8 | 委派 | 指挥 Agent：“请研判 1201 工作面” | 调用 delegate → rescue-assessment，结果返回指挥 Agent | |
| 9 | 预检拦截 | 让指挥 Agent 下发缺少注惰步骤的封闭方案 | 不弹确认，直接拒绝，原因含“第85条”；审计记 denied | |
| 10 | 人工确认 | 下发合规的防灭火方案 | 弹出确认，显示确认人；确认后台账有记录、模拟器 actions 出现对应处置 | |
| 11 | 拒绝确认 | 同上但点拒绝 | 指令未执行；审计记 denied（user rejected） | |
| 12 | 预检不可达 | 停掉 actions 后下发方案 | 拒绝，代码 RESCUE_PRECHECK_UNAVAILABLE | |
| 13 | danger-full-access 下仍拦截 | `DSH_PERMISSION_MODE=danger-full-access` 启动，重复 #9 | 仍被拒绝（预检不依赖审批策略）；记录 #10 在此模式下是否还弹确认 | |
| 14 | 审计事件 | 检查 `var/audit/tool-actions.jsonl` | #9–#12 每次一行，状态正确 | |
| 15 | 上下文压缩 | s3 场景连续研判 2 小时（加速） | 不超上下文；压缩后仍记得已下发的指令 | |
| 16 | 本地大模型 | `scripts/dev.sh --local-llm`（有可用端点时） | 能选中 rescue-local 模型并完成 #8 | |
| 17 | `.env` 加载 | 不 export、只写 `.env` 时启动 | 确认 DSH_* 必须 export 的说法；如有变化更新 `scripts/dev.sh` | |

## 已知需要关注的点

- `rescue-approval` 在 `tools/pre-execute` 里先调 `next()` 再决定 ask：若上游已 deny/cancel，保持上游结论。需确认 rc.4 的瀑布顺序与预期一致（#9、#10）。
- `rescue-audit` 根据错误码和错误信息正则判断 issued / denied / failed（`src/audit/record.ts`）。DSH 改错误文案会导致误判，#11、#12 要核对。
- 确认人按角色限定暂未实现（DSH 侧需要账号/角色能力）。
