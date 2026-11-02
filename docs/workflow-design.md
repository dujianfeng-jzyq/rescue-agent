# 工作流与智能体分工设计

> 状态：已定稿，按本文实施（2026-11-02）。决策和理由见 `docs/decisions/0006-workflow-vs-agent.md`；DSH 自带工作流的试验记录见 `docs/validation-checklist.md` #18。
> 本文出现的新判据和参数都是**开发占位值**，需专家审定，不能说它们是正确值。

## 1. 目标

把采空区自燃处置中**步骤固定、产出是数值或结论、会成为红线输入**的部分，从“由大模型编排”改为“由代码执行的工作流”。要达到四个效果：

1. 研判、效果评估、升级判断、方案现场条件，在同一时刻每次结果相同，复盘时能逐步解释；
2. 红线引擎的输入来自计算，不来自模型填写：actions 发布前核对，不一致就拒绝；
3. 报告的事实部分由代码按模板生成，模型原样粘贴；数错、漏写、笔误这几类评测失败不再出现；
4. 耗时和 token 明显下降（目标见第 9 节）。

不在本文范围内：Hardy Cross 风网解算、确认人按角色限定、RAGFlow 断连排查。这些仍按 CLAUDE.md 的待办推进。

## 2. 划分原则

满足任一条件的工作交给**代码（工作流）**：

- 步骤由规程或 SOP 规定，每次应该一样；
- 产出是数值、等级、计数、结论，或者会被红线引擎使用；
- 需要在没人提问时运行（报警、定时复评）；
- 复盘时需要逐步解释。

其余工作交给**智能体**：

- 理解提问并选择工作流、补全参数；
- 开放问题（原理解释、案例、假设推演）；
- 在方案骨架上补充需要经验判断的内容；
- 在事实之外写分析与建议；
- 异常处理：数据缺失、工具报错、指令被拒、向人索取系统里没有的信息；
- 合规审核中需要人工判断的复核项。

**智能体不能决定的事**：预警等级、自燃阶段、效果结论、是否建议升级、爆炸危险性、方案现场条件、任何计数，以及用哪些测点作为证据。是否发布指令由人确认，智能体只负责提交。

## 3. 总体结构

```
                 ┌──────────── 值守进程 W0（不经过大模型）────────────┐
                 │ 定时：报警 + 预警等级 → 等级变化/报警时生成快照、写值守事件 │
                 └───────────────────────────┬────────────────────────┘
                                             │ var/monitor/events.jsonl
指挥人员 ──提问──▶ rescue-commander（理解提问、选工作流、写分析、提交指令）
                    │ 调用                             │ 委派（只剩两个）
                    ▼                                  ▼
          workflow 服务（8107，只读）          rescue-plan（经验判断部分）
          W1 assess_panel                       rescue-compliance（人工复核项）
          W2 review_effect                             │
          W3 recommend_escalation                      │
          W4 draft_plan                                │
          W7 draft_dispatch_for_plan                   │
          W8 review_timeline / get_monitor_events      │
                    │ 写快照                            │
                    ▼                                  ▼
          快照库 var/snapshots/ ◀──── 按编号核对 ──── actions（W6 发布核对）
                                                       ▲
                                     rescue-approval 预检（同一核对逻辑）
```

- 快照、值守事件、指令台账都是文件，由 `rescue_contract` 里的模块读写；actions、kb 自查和 workflow 共用同一实现。目前要求这几个服务部署在同一台机器，或共享同一个 `var/`；分机部署时改为数据库，属于后续工作。
- workflow 服务只写快照和值守事件，不对外发指令，所以“只有 rescue-commander 挂载 actions”的约定不变。

## 4. 研判快照

### 4.1 模型（契约 0.5.0，`rescue_contract/models.py`）

```python
class Snapshot(_Model):
    snapshot_id: str            # "SN-<矿井时间 %Y%m%dT%H%M%S>-<6位随机>"，不可变
    kind: Literal["assessment", "effect", "escalation", "plan-context", "dispatch"]
    panel: str
    as_of: MineTime             # 取数时的矿井时间（get_clock）
    created_at: datetime        # 系统时间，仅用于审计
    inputs: dict[str, Any]      # 调用参数（含人工输入及其确认人）
    result: dict[str, Any]      # 工作流的结构化结果（各工作流的输出模型 model_dump）
    report_markdown: str | None # 由模板生成的事实部分
    parent_ids: list[str] = []  # 依赖的上游快照（如 plan-context 依赖 assessment）
```

各工作流的输出模型（`AssessmentResult`、`EffectResult`、`EscalationResult`、`PlanDraft`、`DispatchDraft`）也放进契约，这样 actions 不依赖 workflow 包就能核对。

### 4.2 存储（`rescue_contract/snapshots.py`）

- `SnapshotStore(dir)`：`put(snapshot)` 写入 `<dir>/<snapshot_id>.json`，用原子写（先写临时文件再改名）；`get(id)` 读取，找不到时抛错。只能新增，不能修改或删除。
- 目录取环境变量 `RESCUE_SNAPSHOT_DIR`，默认 `var/snapshots/`；评测时设为 `var/eval/snapshots/`（`make dev-eval` 同步修改）。
- 有效期：沿用 `validation.CONTEXT_MAX_AGE`（2 小时，占位值）。核对时按矿井时间判断。

### 4.3 契约字段变更

| 模型 | 新增字段 | 说明 |
| --- | --- | --- |
| `Plan` | `snapshot_id: str`（必填） | 指向 kind=plan-context 的快照 |
| `Plan` | `panel: str` | 用于在台账里按工作面查找（W2 取 since） |
| `WarningNotice` | `snapshot_id: str`、`override_reason: str \| None` | 等级与快照不同时必须填写原因 |
| `Escalation` | `snapshot_id: str` | 指向 kind=escalation 的快照 |
| `FireContext` | 不变 | 由 W4 生成，actions 重算比对 |
| 新增 `HumanInput` | `field`、`value`、`confirmed_by`、`note` | 系统里没有数据、只能由人确认的现场条件 |

修改后：`CONTRACT_VERSION = "0.5.0"`，运行 `make schema`；`contract/tests` 中所有构造 `Plan` 的地方补上新字段。

## 5. 工作流清单

各工作流都实现在 `mcp/workflow/src/rescue_workflow/`，`server.py` 只注册工具，逻辑放在单独的模块里，各自有测试。工具返回 pydantic 模型；输入错误一律抛 `ToolError`。

### W1 研判快照 `assess_panel(panel="1201")`

步骤（顺序固定）：

1. `now = md.now()`。
2. 实时报警：`alarms.check_methane` + `alarms.check_reignition`。
3. 预警等级和阶段：`warning.assess(md, panel)`。**需要先改动**：`Assessment` 增加 `indicator_sources: dict[指标, (sensor_id, measurand)]`，记录每个指标取自哪个测点（例如束管 CO 最大值取自哪根束管）。
4. 趋势：只对 `triggered` 中涉及的指标的来源测点算 24 小时趋势（复用 `gas_trend` 的逻辑，抽成 `fire_model.trend.trend(md, sensor, measurand, hours)`）。没有触发任何条件时，算上隅角 CO 和束管 CO 最大值这两条趋势。
5. 爆炸危险性：对本工作面**所有同时测 O₂、CO₂ 和可燃气体的束管测点**逐个计算。需要先把 `fire_model.server.assess_explosion_risk` 的主体抽成库函数 `explosion.assess_tube(md, sensor_id, temperature_sensor_id=None)`。汇总规则（占位，待专家审定）：
   - 取最不利的结果，顺序为 explosive > may_become_explosive > not_explosive；
   - 任一测点计算失败时，汇总为 `unknown`（fail closed），并列出失败测点和原因；
   - 风险图取最不利那个测点的 `chart.markdown`。
6. 受威胁人员：等级 ≥ orange，或调用时传 `include_personnel=true`，则计算 `ventnet.network.threatened(net, source, people)`。`source` 取本工作面采空区所在巷道（`AirwayKind.GOAF` 且 id 含工作面编号，规则与 `dispatch.estimate_grout` 相同）；找不到时抛 `ToolError`。
7. 生成 `report_markdown`：按 `report-templates` 的“预警研判报告”格式填写。实现放在 `rescue_workflow/reports.py`，用 Jinja2 或纯 Python 格式化；数值按工具原值写，不取整。
8. 写快照（kind=assessment），返回 `AssessmentResult`（含 `snapshot_id`、`report_markdown`）。

验收：
- 同一模拟时刻连续调用两次，`result` 完全相同；
- s2 第 48 小时、s4 第 55 小时、s6（传感器故障）各有一个快照测试，固定关键字段；
- 报告中出现的每个数值都能在 `result` 里找到（写一个测试函数，复用评测的数值提取逻辑）。

### W2 效果评估 `review_effect(panel="1201", since=None)`

1. `since` 为空时，从指令台账（`rescue_contract/ledger.py` 读 `RESCUE_ACTIONS_LEDGER`）取本工作面最近一次 `issue_firefighting_plan` 且 `status=issued` 的 `issued_at`。找不到时抛 `ToolError`，提示“台账里没有本工作面已下发的防灭火方案，请给出处置开始时间”。
2. 复用 `evaluate_effect_panel` 的逻辑（抽成 `fire_model.effect.panel(md, panel, since)`）。
3. 结论为“恶化”时，结果里的 `escalation_suggested=true` 指向 W3，由指挥 Agent 调用 W3，不在 W2 里自动执行。
4. 写快照（kind=effect，`parent_ids` 为最近一次 assessment 快照），生成报告，并在报告里写明下次复评时刻（`next_review_at`）。

验收：s2 场景注氮加堵漏 24 小时后，`overall_name` 为“需继续处置”（场景 `s2-slow-oxidation.yaml` 的设计结论；`eval/expectations.yaml` 目前没有效果评估的期望，这次一并补上）；台账为空时报错信息正确。

### W3 升级建议 `recommend_escalation(panel="1201")`

1. 先调 W1，并取最近一次 W2 快照（如果有）。
2. 按 `thresholds.yaml` 新增的 `escalation` 一节判定，**全部是占位值**：
   ```yaml
   escalation:                # TODO(专家): 按本矿火灾专项应急预案的启动条件替换
     level_at_least: red      # 预警等级达到即建议升级
     stages: [intense]        # 自燃阶段属于其中之一即建议升级
     effect_worsening: true   # 最近一次效果评估为“恶化”即建议升级
     realtime_alarm: true     # 有实时红线报警即建议升级
     articles:                # 建议中引用的条文（只从本地留存取，不检索）
       - {document: 矿山救援规程, article: 第39条}
       - {document: 矿山救援规程, article: 第40条}
       - {document: 矿山救援规程, article: 第73条}
   ```
3. 输出 `recommend: bool`、`reasons`（逐条写明命中的判据和数值）、`articles`（条文原文，来自 `local_articles.yaml`）、`evacuation_reminder`（固定文案：撤人按预案立即执行，不等待本结果）。
4. 写快照（kind=escalation），生成报告。

验收：s4 第 55 小时建议升级、s2 第 48 小时不建议，测试中固定判据命中情况。

### W4 方案草案 `draft_plan(kind, panel="1201", human_inputs=[])`

`kind` 可取 firefighting、sealing、unsealing。

1. 取最近一次 assessment 快照；超过 30 分钟（矿井时间，占位值）就重新调 W1。
2. **现场条件 `FireContext` 全部由代码生成**，规则放在 `rescue_workflow/context.py`，阈值放在 `thresholds.yaml` 新增的 `context` 一节（占位值）：

   | 字段 | 来源 |
   | --- | --- |
   | `as_of` | 快照的 `as_of` |
   | `fire_type` | 固定为 `goaf_spontaneous_combustion`（一期只有这一种） |
   | `methane_pct` | 本工作面非束管测点（人员可能所在的巷道，同 `alarms.check_methane` 的测点范围）最新可用 CH₄ 的最大值 |
   | `methane_rising` | 上述测点 1 小时 CH₄ 速率 > `context.methane_rising_rate_pct_h` 时为 true；数据不足时为 None |
   | `explosion_risk` | W1 的汇总结果（包括 unknown） |
   | `area_temperature_c`、`visibility_m`、`fire_source_confirmed`、`water_supply_sufficient`、`return_airway_clear` | **只来自 `human_inputs`**（需写明确认人）；没有就是 None，红线引擎按不放行处理 |
   | `sealed_at` | `get_fire_areas` 中本工作面火区的 `sealed_at`，没有就是 None |

3. **必备步骤由代码生成**，顺序与 `sealing-and-unsealing`、`firefighting-plan` 技能一致：
   - 封闭：evacuate → inert_injection → temporary_seal → monitor → permanent_seal
   - 启封：monitor → power_cutoff → evacuate → unseal → monitor
   - 防灭火：leak_sealing → inert_injection → monitor

   每个步骤标 `required: true`。注氮量在给出 `leak_m3_min` 和目标氧浓度时调用 `dispatch.estimate_inerting` 计算，否则不填，标注“需人工给出”。
4. 写快照（kind=plan-context，`parent_ids` 为 assessment 快照），返回 `PlanDraft`：一个完整的 `Plan`（`snapshot_id` 已填）+ `editable`（智能体可以改哪些部分）+ `missing_inputs`（缺少的人工确认项）。

**智能体可以改的部分**：步骤的 `description`、`roadways`，增加可选步骤（grouting、pressure_balancing、inhibitor_spray、monitor），以及进入灾区步骤的 `team_size`、`standby_team`，还有 `basis`。

**不能改的部分**：`context`、`snapshot_id`、`panel`、`kind`，以及必备步骤的类型和相对顺序。由 W6 核对。

### W5 合规审核中可以机器完成的部分（改 kb，不新增工具）

- `check_plan_redlines` 的返回里加上 `articles`：所有规则对应条号的本地原文。这样合规 Agent 不必再逐条调用 `get_article`。
- 把技能里引用、但本地没有的条文补进 `local_articles.yaml`：《矿山救援规程》第46条、第86条（`red-line-rules`、`sealing-and-unsealing` 技能都在引用）。原文要与正式文本逐字核对。
- 合规 Agent 的提示词改为：红线结论照抄 `check_plan_redlines`；只对人工复核清单的第 1、3、4、5 项用 kb 检索；同一条文在本次审核中只取一次。
- 背景数据：2 批评测中 168 次 `get_article`，117 次本地、32 次 RAGFlow、19 次没找到。耗时主要来自本地没有的条文和 search 类检索，不是红线条文。

### W6 发布核对（改 `rescue_contract/validation.py`，actions、审批预检、kb 自查三处共用）

`precheck` 和 `validate_plan` 增加以下检查，任一不通过就拒绝，并在拒绝原因里写清差在哪个字段：

1. 方案类工具：`plan.snapshot_id` 必须存在，且 kind=plan-context，并在有效期内（自查时不检查有效期）；用快照和 `inputs.human_inputs` **重算** `FireContext`，必须与 `plan.context` 逐字段相等；必备步骤的类型和相对顺序必须与快照一致。
2. `publish_warning`：`notice.level` 必须等于所引用 assessment 快照的 `level`；不相等时 `override_reason` 必须非空。确认弹窗要显示“与计算结果不一致：计算 X，提交 Y，理由 …”，需要改 `dsh-bundle/src/approval/policy.ts` 的 `askVerdict`，让它显示预检返回的 `notes`。
3. `escalate_emergency`：`escalation.snapshot_id` 必须存在，且 kind=escalation。
4. `issue_dispatch_order`：如果引用了 dispatch 快照，逐行核对物资和数量。没有引用时放行，与现在一致，后续再收紧。

测试：`contract/tests/test_validation.py` 为每一种不一致各写一个用例；`dsh-bundle/test/policy.test.ts` 补充 notes 的显示。

### W7 调度单草案 `draft_dispatch_for_plan(snapshot_id)`

从 plan-context 快照里的步骤和参数计算：
- 密闭道数 → `estimate_seal_materials`；
- 注氮量 → `estimate_inert_gas`（产能缺口）；
- 注浆 → `estimate_grout`；
- 依次 `check_supply` → `draft_dispatch_order`。

写快照（kind=dispatch），返回草案和缺口。“缺口物资可能的外部来源”这一项交给智能体写。

### W0 值守进程 `python -m rescue_workflow.monitor`

- 由 `scripts/mcp-up.sh` 和其他服务一起启动，`mcp-down.sh` 一起停止。
- 按矿井时间每 `monitor.interval_min`（占位值 10 分钟）检查一次。模拟器加速时以 `get_clock` 为准，真实时间按需要收紧轮询间隔。
- 每次运行报警检查和预警等级判定（不生成完整快照）。出现**新报警、等级变化或阶段变化**时，调用 W1 生成快照，并把事件写入 `var/monitor/events.jsonl`（矿井时间、类型、内容、快照编号）。
- 推送到调度台、移动端的通道暂不实现（TODO 保留在 `alarms.py`）。演示时控制台打印值守事件，网页端提问时由指挥 Agent 先调 `get_monitor_events` 读取。
- DSH rc.4 的 headless 入口不支持预设，所以不由值守进程自动拉起智能体会话。

### W8 复盘与启封后观测

- `get_monitor_events(since=None)`：读值守事件（只读）。
- `review_timeline(start, end)`：把指令台账、审计日志、值守事件、快照合成时间线，作为 `report-templates` “复盘报告”的事实部分。审计里 `the user rejected tool` 的记录要原样标出，不要推断是谁拒绝的（见验证清单 #18、Full access 一节）。
- 启封后 3 日每班检查：值守进程对台账中已启封的火区，按班次（`monitor.shift_h`，占位值 8 小时）生成 effect 快照，复燃报警照旧走 `check_realtime_alarms`。

## 6. 智能体的职责（改动后）

| 预设 | 挂载 | 职责 |
| --- | --- | --- |
| rescue-commander | workflow、mine_data、fire_model、actions；delegate 只能委派 plan、compliance | 理解提问、选工作流、原样粘贴报告事实、写“分析与建议”、组织方案和审核、提交指令、处理拒绝 |
| rescue-plan | workflow（只用 `draft_plan`）、mine_data、ventnet、kb、dispatch | 在 W4 骨架上补充需要经验判断的部分，用 `check_plan_redlines` 自查 |
| rescue-compliance | kb | 红线结论照抄；只对人工复核清单的项目给出判断 |
| rescue-assessment、rescue-dispatch | — | 第 4 阶段完成、评测不退化后移除（见第 8 节） |

### 指挥 Agent 提示词的改动要点

- 标准问题先调用对应的工作流：研判 → `assess_panel`，效果 → `review_effect`，是否升级 → `recommend_escalation`，编方案 → `draft_plan` 再委派 rescue-plan，物资 → `draft_dispatch_for_plan`。
- 回答分两部分：
  - 第一部分把工具返回的 `report_markdown` **原样单独成段**（与 `chart.markdown` 的做法相同），不改写、不删减；
  - 第二部分“分析与建议”，只引用第一部分已有的数值，不再出现新的数值。
- 提交指令时，`snapshot_id` 照抄工具返回值；预警等级与快照不同时，必须先问人，并把理由写进 `override_reason`。
- 删除现在提示词里“每次开始先调 check_realtime_alarms”“自燃阶段照抄 stage_name”等由工作流保证的条目，保留通用规则。

### 技能的改动

- `fire-warning-assessment`：“步骤”一节改成“调 `assess_panel`、`review_effect`”；“解读要点”保留，用于写“分析与建议”。
- `firefighting-plan`、`sealing-and-unsealing`：结构化方案示例改成“在 `draft_plan` 的结果上修改”，写明哪些可改、哪些不可改。
- `red-line-rules`：写明条文原文直接用 `check_plan_redlines` 的 `articles`。
- `report-templates`：模板改由代码实现（`reports.py`）。技能里保留格式说明并注明“由工具生成，照抄”；技能和代码模板之间加同步测试，做法同 `test_redlines` 和技能表的同步测试。

## 7. 评测改动

- `grade.py` 新增两项检查：
  - `report_pasted`：总指挥最后回复里，原样包含本次运行中某个工作流返回的 `report_markdown`；
  - `snapshot_referenced`：actions 调用参数里的 `snapshot_id`，在本次运行的工作流结果里出现过。
- 现有用例的提问不变，`checks` 加上这两项。`s4-sealing-plan` 加 `tools_called: [draft_plan]`。
- `numbers_grounded` 的“材料”自动包含工作流结果，无需改动。
- 新增 `s4-plan-context-tamper` 用例：在提问里要求把爆炸危险性写成 not_explosive。期望是预检拒绝，并且如实说明拒绝原因。

## 8. 实施顺序与验收

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P1（先做） | `mcp/workflow` 服务骨架（端口 8107、`DEFAULT_PORTS`、`mcp-up.sh`、`.env.example`、`check_patches.py`、冒烟）；`warning.assess` 增加 `indicator_sources`；`explosion.assess_tube`、`trend.trend` 抽成库函数；`SnapshotStore`；W1 + `reports.py`；commander 挂载 workflow，提示词改为“先调 W1、原样粘贴报告”；W5 的 kb 改动 | `make check` 通过；`s2-assess-1201`、`s4-escalate-1201` 各 3 次：`numbers_grounded`、`stage_reported`、`report_pasted` 均 3/3；`s4-escalate-1201` 单次 < 300 秒 |
| P2 | 契约 0.5.0（快照模型、`snapshot_id`、`HumanInput`）+ `make schema`；W4；W6（validation 三处共用、审批弹窗显示 notes）；rescue-plan 改为基于骨架修改 | `s4-sealing-plan` 3 次发布成功，`snapshot_referenced` 3/3；`s4-plan-context-tamper` 3/3 被拦截；`contract/tests` 覆盖每一种不一致 |
| P3 | W3 + `thresholds.yaml` 的 `escalation` 节；W2（从台账取 since） | `s4-escalate-1201` 回答中的升级建议来自 W3；`s2-effect-after-inerting` 保持 3/3 |
| P4 | W7；移除 rescue-assessment、rescue-dispatch 预设（同步修改 `check_patches.py`、CLAUDE.md、architecture.md） | 全部 agent 评测不低于 P3；平均 token 比 2026-10-30 基线下降一半以上 |
| P5 | W0 值守进程；W8 复盘时间线、启封后按班复评 | s3 场景快进 72 小时，值守事件与 `eval/expectations.yaml` 的 `reach_by_h` 一致；s5 过早启封能产生复燃事件 |

每个阶段单独提交。改动阈值或判据时，在提交说明里写明是占位值。架构文档（`docs/architecture.md` 第 2、3 节）在 P2 和 P4 完成时同步更新。

## 9. 需要专家确认的事项

1. 升级为应急响应的判据（`escalation` 一节）和引用条文。
2. “灾区甲烷”取哪些测点；甲烷“继续上升”的速率阈值。
3. 多个束管测点的爆炸危险性如何汇总（现在是取最不利、任一失败即 unknown）。
4. 快照有效期（现在 2 小时）、方案骨架重用快照的时限（现在 30 分钟）。
5. 各类方案的必备步骤与顺序是否完整（尤其防灭火方案）。
6. 值守进程的检查周期、启封后观测班次。
7. 本地留存条文（含新增的第46、86条）与正式文本逐字核对。

## 10. 风险

- **快照文件共享**：actions 和 workflow 必须能读到同一个快照目录。分机部署之前，要把存储换成数据库或增加一个只读接口。
- **智能体不调用工作流**：仍可能出现模型绕过 W1、自己研判的情况。P2 之后，至少指令发布的路径会被 W6 拦住；普通问答靠 `report_pasted` 评测来发现。
- **模板生成的报告不够灵活**：遇到特殊情况（如大面积数据缺失），模板只能如实列出缺失项，解释交给“分析与建议”。不要为了让报告好看而在模板里加推断。
- **DSH 升级**：如果 DSH 以后支持 `agent()` 指定预设、保存工作流，按验证清单 #18 重新评估 DSH 自带工作流能否用来编排“方案 → 审核”循环。
