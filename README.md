# 煤矿应急救援智能体（rescue-agent）

基于 DeepSeek Harness（DSH，锁定 0.2.1-rc.4）的煤矿应急救援智能体。一期闭环场景：**采空区煤自燃**——从预警研判、防灭火方案、物资调度，到升级、撤人、封闭与启封。

开发和试运行都对着**模拟矿井**进行；接入真实矿井时只替换 mine-data 适配器，其余不动。

> 方案文档：[docs/implementation-plan.md](docs/implementation-plan.md)（总体方案和路线）；现行结构见 [docs/architecture.md](docs/architecture.md)，各阶段的实施记录见 [docs/progress-log.md](docs/progress-log.md)。
>
> 状态：第二至第五版都已实施并合入 `main`：rescue-agent（专家型智能体）+ rescue-mine（矿井接口）+ guard（对外动作的唯一通道）+ 常驻监听器 sentinel（不经模型，发现异常写事件并唤醒值班会话）+ 通风图地图界面与撤人路线（第四版）+ 按预警等级的演示导演、处置选项卡片、批准超时自动处理（第五版，`docs/design-v5-demo-flow.md`）。
> 数据契约、红线引擎、模拟矿井（含演示导演与控制台）、mine_data 与 actions（guard）两个 MCP 服务、监听器、DSH bundle（证据/固定流程/知识库/审批/审计/事件/唤醒/地图插件）、技能和评测都能运行并有测试；
> 预警阈值、场景参数、技能内容都是**开发占位**，必须经专家审定后才能用于任何正式场合。

## 架构一览

```
 模拟矿井 mine_data:8101（rescue-mine 接口） ◀── sentinel 监听器（按矿井时钟检测，写事件库 var/incidents/）
        ▲                                              │ 事件告警
        │ 读（采集证据）  写（通知/调度/处置，写入令牌）   ▼
 ┌──────┴──────────────── DSH 网页端 ─────────────────────────────┐
 │ rescue-commander（唯一主预设，专家型智能体：自己研判、自己选处置）    │
 │ 插件：证据 / 固定流程 / 知识库 / 事件 / 唤醒 / 审批 / 审计          │
 │ 技能：skills/<技能名>/（流程、脚本、模板、参数）                    │
 └──────────────┬──────────────────────────────────────────────┘
                │ MCP（streamable-http）
          actions:8106（guard：校验 → 经 rescue-mine 执行 → 写台账）
```

三道防线（对外动作）：① rescue-approval 调 guard 预检，不可达则拒绝；② 按动作分级表（C 级）人工确认，批准超时后按表处理（只有低风险的加密监测自动放行，其余只升级提醒、不自动执行）；③ guard 服务端再按同一红线引擎、记录引用和数据时效校验一次。详见 [docs/architecture.md](docs/architecture.md)、[docs/design-v3.md](docs/design-v3.md) 与 [ADR 0009](docs/decisions/0009-demo-director-and-operator-cards.md)。

## 目录

| 路径 | 内容 | 负责人（建议） |
| --- | --- | --- |
| `mcp/contract/` | 数据契约（pydantic）、红线引擎、方案校验、一致性测试、服务启动公共代码 | 架构 |
| `mcp/mine-data-sim/` | 模拟矿井（rescue-mine 接口的一个实现）：采空区自燃过程模型、传感器故障、处置响应、加速时钟、演示模式与控制台 `/console`、演示导演（按预警等级分段推进） | 模拟矿井 |
| `mcp/sentinel/` | 常驻监听器：按矿井时钟检测、写事件库、批准超时升级，不经模型 | 后端 |
| `skills/` | 专家经验与算法的唯一来源：SKILL.md、纯函数脚本、模板、参数、自检样例（规范见 `skills/README.md`） | 专家 + 智能体 |
| `mcp/actions/` | guard 守卫服务：发布预警、下发方案/调度单/撤人通知、升级、启封审批；指令台账 | 后端 |
| `dsh-bundle/` | DSH bundle：证据、固定流程（含处置选项和现场条件卡片）、知识库、事件与唤醒、人工确认与红线预检（含批准超时处理）、审计、地图插件，主预设 rescue-commander | 智能体 |
| `scenarios/` | 6 个标准场景（演示脚本 = 评测集 = 回归集）；`scenarios/demo/flow.yaml` 是演示导演的分支剧本 | 模拟矿井 + 专家 |
| `eval/` | 确定性基线评测、智能体端到端评测（`eval/agent/`）、演示故事线脚本 `story.py`、导演驱动的演示脚本 `demo_run.py` | 测试 |
| `profiles/` | 可选配置：本地大模型、真实矿井环境变量模板 | 运维 |
| `scripts/` | 安装、启动、冒烟、模拟控制台、patch 离线校验 | — |
| `docs/` | 实施方案、现行架构（`architecture.md`）、进度日志（`progress-log.md`）、各版本设计、设计决策（ADR 0001–0009）、真实 DSH 验证清单、评测方案与结果 | — |

## 快速开始

需要：Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)；做 DSH 部分还需要 Node.js 22+、pnpm、`@deepseek-ai/dsh@0.2.1-rc.4`；Docker 演示需要 Docker（含 compose）。

### 演示（Docker，推荐）

```bash
vim .env            # 至少填 DEEPSEEK_API_KEY（RAGFlow 配置可选）
make demo-up        # 构建并启动 4 个容器（mine-sim、actions、sentinel、dsh），等健康检查后打印地址
```

- 网页端 `http://<主机>:13080`；演示控制台 `http://<主机>:13090/console`，登录令牌是 `var/demo/tokens.env` 里的 `RESCUE_SIM_CONTROL_TOKEN`。
- 矿井默认连续运行在常态，可以先讲解设计思路和界面、点地图让智能体研判。**在控制台“演示导演”区块点一次“开始演示”**，之后导演自己推进：监听器告警 → 唤醒值班会话 → 智能体研判；黄色自动提交加密监测，橙色弹**处置选项卡片**（勾选追加注浆、堵漏，不追加点跳过），红色智能体撤人、封闭（封闭方案先答**现场条件卡片**）。你在网页端回答卡片、点审批弹窗的 Allow once；走向由所选处置决定（橙色累计 3 项回落到黄色，只做 1 项升到红色），走完自动结束。演示中出现提权确认一律拒绝。
- 带地图包（`var/map/`）时工作面名要对应：`DEMO_PANEL=211016 make demo-up`。
- `make demo-reset`（同样要带 `DEMO_PANEL`）清空 `var/demo/` 的演示数据并回到常态，是重来一遍最干净的做法；`make demo-down` 停止，`make demo-logs` 看日志。
- 整条故事线（黄 → 橙 → 红 → 封闭 → 复评 → 启封 → 复盘 → 关闭事件）可由脚本扮演操作员自动走一遍：`make story`（本机用 `make dev-story`，Docker 见 `eval/agent/story.py` 的说明），约 12 分钟、14 项检查；`eval/agent/demo_run.py` 是上面“点一次开始演示”那套流程的脚本版（约 5 分钟）。

### 本机开发

```bash
make setup          # 或 scripts/setup.sh；只做 MCP/模型开发：make setup-py
vim .env            # 至少填 DEEPSEEK_API_KEY
make dev            # 启动 mine_data、actions、监听器和 DSH 网页端（RESCUE_SENTINEL=0 不启动监听器）
```

不需要大模型也能跑的部分：

```bash
make test           # Python + TS 单元测试
make up && make smoke   # 启动服务（含监听器）并冒烟
python3 scripts/sim.py reset s4-escalation   # 模拟控制台：reset/normal/inject/advance/apply/override
python3 scripts/sim.py advance 24
make eval           # 基线评测：逐小时扫描标准场景，检查预警时效和误报
make skills-check   # 技能自检
make check          # 提交前全套检查
```

智能体端到端评测：`make dev-eval` 后 `make eval-agent ARGS="--url '<DSH 地址带 token>' --model deepseek-official/deepseek-flash --repeat 3"`，见 [eval/README.md](eval/README.md) 与 [docs/eval-consistency.md](docs/eval-consistency.md)。

切换本地化部署的大模型（OpenAI 兼容接口，不限 DeepSeek）：在 `.env` 填 `RESCUE_LLM_*`，然后 `scripts/dev.sh --local-llm`。

## 可以并行推进的工作

骨架把各部分用**契约**隔开，下面几条线可以同时开工、互不阻塞：

1. **专家审定线**：`skills/*/SKILL.md` 与 `skills/*/references/`（阈值、判据参数）、`scenarios/*.yaml` 参数、`eval/expectations.yaml`。改完跑 `make check`。
2. **模拟矿井线**：校准过程模型（程序升温数据）、补通风网络解算（Hardy Cross）、补水灾/顶板场景的扩展点。对外只要保持 `rescue_contract` 契约和 `python -m rescue_contract.conformance` 通过。
3. **算法线**：技能脚本（`skills/*/scripts/`），有自检样例（`make skills-check`）和基线评测兜底。
4. **知识库线**：和 RAGFlow 部门对接数据集；检索与条文索引在 `dsh-bundle/src/knowledge`、`scripts/kb`。
5. **智能体线**：预设提示词、委派策略、确认交互；先完成 [docs/validation-checklist.md](docs/validation-checklist.md) 里的真实 DSH 验证项。
6. **真实矿井适配线**：照 [docs/rescue-mine-interface.md](docs/rescue-mine-interface.md)（代码生成）写适配器，用 `profiles/mine-template.env.example` 切换。

改动契约（`mcp/contract/src/rescue_contract/models.py`）需要架构负责人评审，并 `make schema` 重新导出 JSON Schema（CI 会检查）。

## 约定

- 所有数值来自工具计算，Agent 不自行估算；报告注明来源。
- 只有指挥 Agent 挂载 actions；其他 Agent 只读（`scripts/check_patches.py` 会检查）。
- 新增红线规则：改 `rescue_contract/redlines.py` + 测试 + `skills/plan-compliance/SKILL.md`（有同步测试）。
- 指令台账、事件库 `var/incidents/`、审计日志 `var/audit/tool-actions.jsonl` 是复盘依据，不手工修改。
