# 架构说明

本文说明骨架里各部分怎么连接、为什么这样分。设计取舍的来龙去脉见 `docs/decisions/`。

## 1. 分层

| 层 | 形式 | 放什么 | 不放什么 |
| --- | --- | --- | --- |
| 智能体编排 | DSH Agent 预设（`dsh-bundle/presets/`） | 角色人设、可用工具、委派关系、上下文压缩 | 业务计算 |
| 流程知识 | 技能（`skills/`） | SOP、报告格式、何时调用哪个工具、红线说明 | 数值、阈值、规则判定 |
| 横切控制 | DSH 原生插件（`dsh-bundle/src/`） | 红线预检 + 人工确认、审计 | 业务逻辑 |
| 业务能力 | MCP 服务（`mcp/`） | 数据接入、计算模型、知识检索、指令出口 | 对话、提示词 |
| 确定性流程 | workflow 服务（`mcp/workflow`） | 研判、效果评估、升级建议、方案草案等标准流程的固定步骤，研判快照，值守 | 对话、需要经验判断的内容 |
| 契约 | Python 包 `rescue_contract` | 数据模型、红线引擎、方案校验 | 任何具体矿井的数据 |

原则：**会算错的东西放在可测试的代码里，会说错的东西放在技能里，会做错的东西放在插件里拦住。**
步骤固定、产出是数值或结论、会成为红线输入的流程，由 workflow 服务按固定步骤完成并写成研判快照；智能体只做理解提问、开放问答、经验判断和异常处理（ADR 0006，`docs/workflow-design.md`）。

## 2. Agent 与工具

| 预设 | 挂载 MCP | 其他 | 说明 |
| --- | --- | --- | --- |
| rescue-commander（默认） | mine_data, fire_model, actions, workflow | delegate、ask_user、todo | 唯一能下指令的 Agent；标准流程调 workflow |
| rescue-assessment | mine_data, fire_model, ventnet, kb | — | 预警研判、指挥部研判报告 |
| rescue-dispatch | mine_data, dispatch | — | 物资、队伍、调度单草案 |
| rescue-plan | mine_data, ventnet, kb, workflow | — | 在 draft_plan 草案上补充需要经验判断的部分，含红线自查 |
| rescue-compliance | kb | — | 条文检索、方案合规审查 |

## 3. 一次指令的路径

```
指挥 Agent 调用 mcp__actions__issue_sealing_plan(plan)
  │
  ├─ DSH tools/pre-execute ── rescue-approval
  │     1. POST actions:/redlines/check {tool, arguments}
  │        └ rescue_contract.validation.precheck：方案类型 + 数据时效(≤2h) + 研判快照核对 + 红线引擎
  │        不通过 → deny(RESCUE_RED_LINE)；不可达/超时 → deny(RESCUE_PRECHECK_UNAVAILABLE)
  │     2. 通过 → ask：界面弹出确认（展示方案摘要和确认人角色）
  │
  ├─ 人工确认
  │
  ├─ actions 服务端：validate_plan 再校验一次（不信任客户端）
  │     不通过 → ToolError("指令未发布。…")
  │     通过 → 写指令台账 var/actions-ledger.jsonl；模拟环境下转发给模拟矿井（RESCUE_SIM_CONTROL_URL）
  │
  └─ DSH tools/result ── rescue-audit 写 var/audit/tool-actions.jsonl（issued / denied / failed）
```

方案起草时，rescue-plan 先调 workflow 的 `draft_plan` 取得由代码生成的现场条件和必备步骤（写成方案草案快照），改完再调 `kb.check_plan_redlines` 自查（同一个引擎、同一套快照核对），所以正常流程里第①道很少触发；它是兜底。

**研判快照核对**：方案的 `snapshot_id` 必须指向 draft_plan 生成的快照，`context` 逐字段相等、必备步骤齐全且顺序不变；
预警的等级必须等于研判快照的等级（不同时要写 `override_reason`，确认弹窗里显示）；升级必须引用建议升级的快照；
调度单引用了调度草案快照时逐行核对。快照在 `var/snapshots/`（`RESCUE_SNAPSHOT_DIR`），workflow 写，actions 和 kb 读。

## 4. 模拟矿井

`mine-data-sim` 是集总参数的采空区自燃模型：高温点温度随氧化放热、散热、注惰/灌浆等处置变化；标志气体（CO、C2H4、C2H2…）由温度和供氧推出，再按风流稀释到各测点；叠加确定性噪声和可编排的传感器故障。时钟可加速、暂停、快进。

- 智能体只能通过 MCP 看到“传感器读数”，看不到高温点真实温度（`/sim/state` 的 `truth` 只给控制台和评测）。
- 指令经 actions 确认后才作用于模拟器——和真实矿井“指令下达后由人执行”的关系一致。
- 6 个标准场景同时是演示脚本、评测集、回归集（`scenarios/README.md`）。

接入真实矿井：写一个实现相同 MCP 工具的适配器包（数据来自安全监控、束管、人员定位等系统；输入错误如未知测点要抛 `ToolError` 并写明原因，否则 Agent 看不到；`get_personnel` 用 `PersonnelSnapshot.of(名单)` 构造，人数由契约代码统一计算），先通过 `python -m rescue_contract.conformance <URL>`，再用 `RESCUE_MINE_DATA_MODULE` 替换；`RESCUE_SIM_CONTROL_URL` 置空。

## 5. 知识库

`kb` 是 RAGFlow 之上的薄封装：检索参数、数据集划分、术语改写（如“密闭”↔“封闭”）、结果整理都在这一层，RAGFlow 侧配置有变化时优先在这里适配。RAGFlow 不可用时退回本地红线条文摘要（`local_articles.yaml`），保证红线相关问答和离线开发可用。

## 6. 大模型

开发期用 DeepSeek API（DSH 自带提供方）。本地化部署后用 `profiles/local-llm.patch.yml` 注册 OpenAI 兼容提供方 `rescue-local`，或在 DSH 网页“模型”页添加。代码不依赖具体模型。

## 7. 运行时文件（`var/`，不入库）

| 文件 | 写入方 | 用途 |
| --- | --- | --- |
| `var/actions-ledger.jsonl` | actions | 已发布指令台账 |
| `var/snapshots/*.json` | workflow | 研判快照（只新增不修改），指令发布时按编号核对 |
| `var/audit/tool-actions.jsonl` | rescue-audit | 所有指令类工具调用结果（含被拦截、被拒绝） |
| `var/log/*.log`、`var/run/*.pid` | mcp-up.sh | 服务日志与进程 |
| `var/eval/baseline.json` | eval/baseline.py | 基线评测明细 |
