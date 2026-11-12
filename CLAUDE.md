# CLAUDE.md

煤矿应急救援智能体：基于 DeepSeek Harness（DSH，锁定 0.2.1-rc.4）。一期闭环场景是采空区煤自燃，开发和演示都对着模拟矿井进行。

用中文写文档、注释、提交说明和面向用户的文字；标识符用英文。

## 文档索引

| 要查什么 | 看哪里 |
| --- | --- |
| 现行架构（怎么连起来、一次指令的路径、运行时文件） | `docs/architecture.md` |
| 做了什么、什么时候、评测结果、踩过的坑（第一至第五版逐阶段） | `docs/progress-log.md` |
| 为什么这样做（总体方案和路线） | `docs/implementation-plan.md` |
| 各版本设计 | 第二版 `docs/design-v2.md`；第三版 `design-v3.md`（rescue-mine、常驻监听与事件、专家型智能体）；第四版 `design-v4-map.md`（地图界面）、`design-v4-evacuation.md`（撤人路线）；第五版 `design-v5-demo-flow.md`（演示导演、选项卡片、批准超时） |
| 设计决策 | `docs/decisions/`（ADR 0001–0009） |
| 待专家审定的阈值、判据和参数（集中清单） | `docs/expert-review.md` |
| rescue-mine 接口（代码生成，`make interface-doc`） | `docs/rescue-mine-interface.md` |
| 真实 DSH 上的验证记录与已知问题（#1–#42） | `docs/validation-checklist.md` |
| 智能体一致性检测（期望决策与实际比较） | `docs/eval-consistency.md`；评测用法见 `eval/README.md` |
| 技能规范 | `skills/README.md` |
| 演示知识库的迁移记录（已执行） | `docs/kb-migration.md` |

## 状态（2026-11-10）

第二至第五版都已实施并合入 `main`：技能 + 插件 + 固定流程（二）；rescue-mine / guard / 常驻监听与事件 / 专家型智能体（三）；通风图地图界面与撤人路线（四）；按预警等级的演示导演、处置选项卡片、批准超时自动处理（五）。第一版的 Python 服务已删除。
阈值、场景参数、技能判据、分支参数、选项表都是**开发占位值**，待专家审定，不要声称它们“正确”；新增或发现待审事项时加进 `docs/expert-review.md`。
待办和遗留见 `docs/progress-log.md` 各版本末尾。

## 常用命令

```bash
make setup-py                 # 只装 Python 部分（不需要 DSH）
make setup                    # 全部（需要 node 22+、pnpm、dsh 0.2.1-rc.4）
make test                     # Python + TS 测试
make lint                     # ruff
make check                    # 提交前：lint + 测试 + patch 离线校验 + 基线评测
make up / make down / make smoke   # 启停 MCP 服务（mine_data、actions）和常驻监听器 sentinel（RESCUE_SENTINEL=0 不启动）并冒烟
make eval                     # 确定性基线评测（不经过大模型，运行 fire-assessment 的技能脚本）
make dev-eval                 # 智能体评测环境：同 make dev，但指令台账、记录、事件库和审计写到 var/eval/，且不启动监听器
make eval-agent ARGS="--url '<DSH 地址带 token>' --model deepseek-official/deepseek-flash --repeat 3 [--case <用例 id>]"   # 智能体端到端评测（需先 make dev-eval）
make schema                   # 契约模型、动作分级表、处置选项表、现场条件卡片改动后重新导出（CI 会检查）
make skills-check             # 技能自检（结构、import、样例）
make interface-doc            # 重新生成 rescue-mine 接口说明（工具或模型改动后；测试会检查它是最新的）
python3 scripts/sim.py state|reset <场景>|advance <小时>|apply <处置>   # 模拟控制台（Docker 用 RESCUE_SIM_CONTROL_URL=http://127.0.0.1:13090/sim 和控制令牌）
make dev                      # MCP 服务 + DSH 网页端（需要 DEEPSEEK_API_KEY）
make demo-up / demo-down / demo-reset   # 演示环境（Docker，4 个容器）：网页端 13080、控制台 13090/console，数据在 var/demo/，令牌在 var/demo/tokens.env；工作面名缺省 211016（与地图对应），DEMO_PANEL=1201 换回原名
make dev-story / make story   # 演示故事线（按等级脚本推进，14 项检查）；Docker 里直接跑 eval/agent/story.py，环境变量见其文件头
python -m rescue_contract.conformance <URL> [--with-actions]   # 接口一致性检查；--with-actions 会真的执行通知/调度/处置，只对模拟矿井加
python -m rescue_contract.collect <collect.yaml> <kind> <panel>   # 按采集清单取一次证据（Python 采集小工具）
```

演示（第五版）：控制台“演示导演”区块点一次“开始演示”，之后导演自己推进；`eval/agent/demo_run.py` 是同一流程的脚本版（扮演操作员，`STORY_ORANGE_OPTIONS` 控制橙色卡片选什么：`注氮|注浆|堵漏` 回落、只选一项或 `不采取处置`（在输入框输入）升红、空串 = 点跳过（暂不决定））。
单个包的测试：`cd mcp && uv run pytest contract/tests -q`（包：contract、mine-data-sim、actions、sentinel）；TS：`cd dsh-bundle && pnpm test`；改了 `dsh-bundle/src` 要 `pnpm build`（只改 `client/src` 用 `node client/build.mjs`）。

## 结构与边界（改代码前先看）

- `skills/<技能名>/`：专家经验和算法的唯一来源。SKILL.md（流程）、`scripts/`（纯函数，`--input-file`/`--output-file`，不访问网络和凭据）、`templates/`、`references/`（参数，标注占位值）、`tests/cases/`（自检样例，CI 运行）。
- `dsh-bundle/src/`：`evidence/`（证据插件：采集、`runScript`、记录、`check_mine`）、`fire-loop/`（固定流程、处置选项与现场条件卡片）、`knowledge/`（知识库插件、条文索引）、`incident/`（事件插件、唤醒插件）、`approval/`（红线预检 + 人工确认 + 批准超时自动处理）、`audit/`、`map/`（地图界面，见下）；`presets/commander.patch.yml`（唯一主预设）、`cordis.patch.yml`。插件只做编排，不写领域判据。对 DSH 类型的引用只放在 `src/adapter/dsh.ts`。
- 记录（`Record`）：只有 DSH 进程（证据插件）能写，bash 沙箱和 guard（actions）只读；指令引用 `record_id`。**事件库不是记录库**：监听器的检测摘要不能被指令引用，智能体被唤醒后自己研判、产生记录。
- `mcp/contract/`：数据契约（pydantic）、红线引擎 `redlines.py`、方案校验 `validation.py`、动作分级表 `action_policy.py`、处置选项表 `response_options.py`、现场条件卡片 `field_conditions.py`、事件库 `incidents.py`、`MineData` 客户端、一致性测试、技能自检、模板与规范化 JSON。**契约是各工作线之间的边界**，改 `models.py` 要谨慎并 `make schema`。
- `mcp/mine-data-sim`（模拟矿井，rescue-mine 接口的一个实现；含演示控制台 `/console` 和演示导演 `director.py`）、`mcp/actions`（**guard 守卫服务**：对外动作的唯一通道，校验后经 rescue-mine 执行并写台账）：各自 `src/rescue_<名>/server.py` 里 `build_server()`，用 `rescue_contract.serving.serve` 启动（streamable-http，端口 8101、8106；服务名 `mine_data`、`actions` 没变）。
- `mcp/sentinel`：常驻监听器 `python -m rescue_sentinel`，不经模型；判据只用 `skills/fire-assessment`（`check.py`，参数在 `references/watch.yaml` 的 `sentinel` 节）。事件库 `var/incidents/<事件编号>.jsonl`（只追加、折叠出状态；Python 与 TS `incident/store.ts` 共用 `contract/tests/fixtures/incidents`）。
- `scenarios/*.yaml`：标准场景 = 演示脚本 = 评测集 = 回归集；`scenarios/demo/flow.yaml` 是演示导演的分支剧本（放子目录，免得被当成场景加载）。
- `eval/baseline.py` + `expectations.yaml`：确定性回归基线。`eval/agent/`：智能体端到端评测（`cases.yaml` 用例，`run.py` 经 DSH 网页端 + Playwright 提问并点确认，`grade.py` 读会话日志判定，`story.py` 故事线，`demo_run.py` 导演驱动的演示脚本，`decisions.py` 期望决策比较）。
- 地图（第四版）：`scripts/map/` 把 DWG 转几何到 `var/map/geometry.json`（**地图包，真实矿井图不进仓库**，git 忽略，Docker 里只读挂载 `/data/map`；没有它网页端就是 DSH 原界面）；`dsh-bundle/src/map`（Node 侧，**必须是 bundle 的包根入口 `@rescue/dsh-bundle`**，宿主只扫描包根入口当客户端包）；`dsh-bundle/client/src`（浏览器端，esbuild 打成 `lib/client.js`；悬浮层样式依赖 DSH 的哈希类名，升级 DSH 要重核）；映射表 `var/map/mine_map.yaml`；智能体的 `map_view` 工具（名称只能是地图上有的，标记的标签由代码从记录取）。模拟矿井的工作面名用 `RESCUE_SIM_PANEL=211016` 换成与地图一致（监听器同时设 `RESCUE_WATCH_PANELS`，Docker 用 `DEMO_PANEL`）。

## 必须遵守的约定

1. **数值只来自代码计算**。“代码”指 MCP 服务和技能脚本。Agent 提示词和 SKILL.md 的正文里不写阈值、不让模型估算；新的判断逻辑写进技能脚本并加自检样例或测试。计数、合计、差值、汇总结论也算（如 `threatened_personnel` 的 count、`gas_trend` 的 change、`evaluate_effect_panel` 的 counts/overall、`alert_status` 的已处理判断）。智能体在沙箱里自己运行脚本的结果只作参考，不能被指令引用。**选项、卡片、是否重复投递这类“该问什么、选什么”也由代码给出**，智能体原样使用，不自己编。
2. **只有 `rescue-commander` 挂载 actions（guard）**；其他预设只读（固定流程的阶段子 Agent 用 `tools.guard` 限制为只能提交结果）。`scripts/check_patches.py` 会检查。指令必须引用记录（`record_id`），预警等级、升级结论、封闭方案按记录核对（`validation.py`），不能由模型填写。**哪些指令要弹人工确认、批准超时后怎么办，由动作分级表决定**（`action_policy.py`，导出为 `dsh-bundle/data/action-policy.json`）：C 级（影响矿井现场的处置和对外通知）弹确认，A、B 级不弹，**没有登记的工具按 C 级**；加密监测 `start_monitoring` 只增加观察，是 B 级（不弹确认，仍经 guard 校验、写台账）；`on_timeout` 缺省 `escalate`（只升级提醒，不自动执行），影响现场或不可逆的动作不得设成自动放行（有测试固定）。guard 之外没有任何途径写矿井：写入类工具要写入令牌，只有 guard 持有。
3. **红线规则只在 `rescue_contract/redlines.py` 实现一次**。新增规则：改规则表 + `contract/tests/test_redlines.py` + `skills/plan-compliance/SKILL.md`（有同步测试）。知识库插件需要的条号由规则表导出，不在 TS 里再写一遍。未知值按不通过处理（fail closed）。
4. **智能体看不到模拟器真值**。`/sim/state` 的 truth、读数设置、倍速只给控制台和评测；不要通过 MCP 工具或证据插件暴露。DSH 不持有 `/sim` 控制令牌。
5. **mine-data 的工具集合就是真实矿井适配器要实现的接口（rescue-mine）**。接口表在 `rescue_contract/interface.py`，增删工具要改它、同步 `conformance.py`，并 `make interface-doc`；采集清单（技能的 `references/collect.yaml`）只能用这些工具，且按角色取数（`select: {role, source, panel}`），不写测点编号。写入类工具另认写入令牌（`RESCUE_MCP_MINE_ACT_TOKEN`）。
6. 阈值、场景参数、分支参数、选项表、评测期望都是**开发占位值**。可以为了测试调整，但要在提交说明里写明，并且不要声称它们“正确”。
7. MCP Python SDK 是 2.x：`from mcp.server.mcpserver import MCPServer`（没有 FastMCP）。工具返回 pydantic 模型或 `dict[str, Any]`，不要返回裸 `dict`（会丢结构化输出）。输入错误抛 `ToolError`（`mcp.server.mcpserver.exceptions`）：其他异常的说明传不到 Agent。
8. DSH 版本锁定在 0.2.1-rc.4。DSH 的 `DSH_*` 环境变量必须 export，写在 `.env` 里不生效。
9. 不要改 `var/` 下的台账和审计日志；它们是复盘依据。
10. 向人提问只有四种情况（见 `skills/fire-incident-management/SKILL.md`）：对外动作要批准（弹窗就是提问）、橙色研判后选处置（代码给出的卡片）、缺信息（含封闭方案的现场条件，代码给出的卡片）、关键事实冲突。其余智能体自己决定。

## 已知坑（改相关代码前看）

- **DSH / 插件**：提供 cordis 服务的插件必须放进带 `isolate` 的 `cordis:group`，固定流程插件放同一组才能注入 `rescueEvidence`；DSH 结构化输出的 schema 只支持 type/oneOf/properties/required/additionalProperties/items/enum/const 和注解（`minItems` 会让子 Agent 启动失败，有测试检查）；不要用 `concludeTurn`（rc.4 网页端看不到报告）；provider 重试要把 `STREAM_CLOSED` 加进 `retryPolicy.retryableCodes`；消息来源 `kind` 不能是 `plugin`（用 `rescue-sentinel`）；`createUserMessage` 等 DSH 类型只在 `src/adapter/dsh.ts` 引用。
- **人工确认自动放行**：只返回 `allowed-once` 网页端的确认卡片不会消失，要把 `req.signal` 换成“原信号 + 插件自己的信号”并到点中止（验证清单 #37；依赖请求对象可写，升级 DSH 要重核）。多选提问卡片没选任何项时 Submit 是灰的，只能点 Skip。
- **技能脚本**：由插件起的子进程不在沙箱里、默认继承 DSH 进程的密钥，`runScript` 必须按白名单传环境变量、设超时；用 `python -E -s -B -X utf8` 运行，不用 `-I`；辅助模块不要与标准库内建模块同名（`_io` 会被遮住）；模板是 Markdown 子集（相邻普通行会并成一段，0 是真值，按“有没有”显示要用布尔字段）；`make_skill_cases.py` 全量重新生成会改写大量无关样例，提交前要看 diff。
- **演示环境**：容器间按服务名访问要用 `RESCUE_MCP_ALLOWED_HOSTS` 加白名单（否则 421）；证据插件的脚本运行目录在容器里单独给 `RESCUE_RUN_DIR`；DSH 镜像要装字体（否则 Word 预览显示 “Office previews are unavailable”）；演示中出现提权确认一律拒绝；矿井时钟倒退（模拟矿井重置）时监听器会清掉旧基线；想重来用 `make demo-reset`（比控制台“重置为常态”干净，后者不清旧事件和旧值班会话）。
- **验证界面 / 评测**：不要让本地 DSH 带着密钥跑（DSH 启动会读当前目录的 `.env`，要从别的目录启动，否则值班唤醒插件会真实调用模型）；没有模型密钥的 DSH 会一直弹 “Get started” 遮住所有点击；`pkill -f <脚本名>` 会连执行它的 shell 一起杀，按精确 PID 杀；`ruff --fix` 会删必要的 `# noqa`；评测运行器偶发“没有找到评测会话”（重跑即可）；`make dev-eval` 和 Docker 演示不要同时跑（评测环境没有监听器，Docker 里有）。
- **评测**：用例的 `operator_cards: true` 才会替智能体回答代码给出的卡片，否则运行停在卡片处（status `asked_user`）；`wake: true` 的用例提示词由生成器生成，不要手抄；每个用例大多只跑过 1 次，稳定性没统计。
