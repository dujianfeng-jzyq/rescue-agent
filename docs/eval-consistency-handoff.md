# 智能体一致性检测：交接与待办

> 写于 2026-11-07。给新会话用：读完这份就能接着做，不需要看之前的对话。
> 分支 `eval-facts`（基于 `main`，未推送，未合入）。最近三个提交：`d7009e9`、`42258fe`、`b845de0`。

## 目标

**为所有已实现的功能，按下面这套设计写一致性检测用例**：给定输入 A（场景、时刻、提问或值守事件），期望智能体的决策为 B，运行得到实际 B1，比较 B 与 B1。

“智能体端到端评测”主要验证**执行流程和决策是否正确、下发的指令是否正确**；最终文字回复次要。
因此**主线是 D 类（决策）**，辅以 A 类（过程）和 B 类（关键事实）。**C 类评测员是可选的、低优先级**（见下），不要把时间花在铺开它上。

## 设计（已讨论定稿，用户都同意了）

评测分五类，只有后两类经过智能体：

| 类 | 内容 | 位置 |
|---|---|---|
| 不经智能体 | 确定性基线（`make eval`）、技能自检、单元/集成测试 | 保证代码本身没错 |
| A 过程 | 调了哪些工具、有没有轮询、有没有重复提交、人工确认 | `eval/agent/grade.py` 的规则，读会话日志 |
| B 事实 | 固定流程结果里的结构化事实是否写进回复，不比措辞 | `eval/agent/facts.py`，检查项 `facts_reported` |
| C 语义（**可选，低优先级**） | 评测员（独立模型调用）按封闭式清单作答，答案须带逐字引文，和期望逐题比对 | `eval/agent/judge.py`、`rubrics/`、`calibrate.py`，检查项 `rubric` |
| D 决策 | 用例写好期望决策 B，从日志提取实际 B1 比较 | `eval/agent/decisions.py`、`gen_expected.py`，检查项 `expect_decisions` |

### D 类：`expect_decisions`（写在用例的 `checks` 下）

```yaml
expect_decisions:
  status: placeholder              # 必须标占位，待专家审定（有测试检查）
  derived_from: {...}              # 生成脚本写入的依据，只给人看
  external_actions:                # 对外动作（actions 服务）：封闭集合
    required: {escalate_emergency: {}, publish_warning: {level: red}}   # 至少提交一次；每次提交的参数都要含这些键值
    optional: {notify_evacuation: {}}                                    # 可提交可不提交
  flows_called: [run_fire_assessment]          # 流程至少调用一次
  incident_ops: {required: [], forbidden: [close_incident]}
```

- 对外动作按“提交”算（被人工确认拒绝的也算）；`required`、`optional` 之外的提交算“多做”。
- 参数比较前把唯一的一层对象展开（`{"notice": {"level": "red"}}` 当 `{"level": "red"}`）。
- 没写的部分不检查；`external_actions` 一旦写了就是封闭的。
- **期望 B 尽量由代码在该场景该时刻算出，不手写**：`eval/agent/gen_expected.py` 用技能脚本（研判、升级建议、复评，不经智能体）生成草稿，人审阅后写进 `cases.yaml`（`--write`）。阈值或场景变了重新生成，diff 就是期望的变化。生成脚本里的“占位策略”是我们的猜测，不是专家规则。
- 另有 `flows_forbidden`（这些流程一次都不该调用）、`questions: {min, max}`（向人提问次数范围；缺信息用例 `min: 1`）。
- **2026-11-07 起 `cases.yaml` 里除探测用例外的 22 个用例都有 `expect_decisions`**。`gen_expected.py` 里 `KINDS`（由代码算）和 `HAND`（手写并注明依据）两张表是唯一来源，`--write` 写回。

### 原则（别违反）

1. 期望固定写在用例里，可审阅；**不要用会话里的工具结果反推期望**（循环论证，研判错了也会通过）。
2. 所有期望标 `placeholder`；不声称“正确”（CLAUDE.md 约定 6）。看到智能体行为后调整期望要在提交说明里如实写明，那不是智能体变好了。
3. 不为让评测通过而改人设或加禁令（今天试过“禁止换算单位”，没带来可测量的好处，已回退）。评测不过先判断是**判定方法的问题**还是**智能体的问题**，前者改判定。
4. 对外动作是安全相关的，用确定性规则判，不交给评测员。评测员只管规则写不出来的语义，且必须先用人工标注校准，一致率不够就不启用。
5. **评测员的定位（2026-11-07 与用户讨论后定）**：决策和下发的指令由 `expect_decisions` 判，关键事实由 `facts_reported` 判，最终文字次要；过程类问题（重复投递不该重新研判、数据不可信时不得提交等）优先写成期望（`flows_called` 不应出现某流程、`external_actions` 为空集合），不要交给评测员。评测员只保留一个用途：**文字层面且影响操作员下一步的要求**，目前只有“对外动作被拒绝/失败后是否如实告知”，已有 `s4-warning-rejected` 试点。**不再扩展到其他用例**，除非 `expect_decisions` 和 `facts_reported` 都覆盖不到、而且确实影响操作员决策的文字问题出现。
6. 不改 `var/` 下的台账和审计日志；评测一律 `make dev-eval`（数据写 `var/eval/`）。

## 当前状态

- 全部检查项说明在 `eval/agent/grade.py` 文件头。`checks.facts_reported` 已替换原来的 `report_pasted`（11 处）。
- 评测员（可选，低优先级，见原则 5）只试点了 `s4-warning-rejected`（清单 `rubrics/s4-warning-rejected.yaml`，校准样本 37 份，用例级判定与人工 37/37 一致；标注只有一人，坏回复是手写的）。旧的 `rejection_reported` 与之并存，校准稳定后可以删。
- 最近一轮真跑（deepseek-flash，各 3 次）：11 个用例 31/33 通过；失败 2 次：`s2-effect-after-inerting` 的 `facts_reported`（智能体把“需继续处置”写成“需要继续处置”，按设计保持严格，留给评测员判）、`s4-escalate-1201` 的 `no_wait_polling`（等子 Agent 时偶发轮询，累计约每七八次一次，真实行为问题，未处理）。三个有 `expect_decisions` 的用例 9/9。

## 2026-11-07 完成情况（本节取代下面“待办”里已做的部分）

新增用例（均带 expect_decisions，占位）：`v3-wake-accelerating`（s3 第 57 小时）、`v3-wake-mine-alarm`、`v3-wake-sensor-fault`（s6 第 33 小时）、`v3-wake-repeat-dup` / `v3-wake-repeat-new`（重复投递一对）、`v3-wake-approval-timeout`、`v3-ask-lab-sample`（缺信息必须提问）、`v3-notify-evacuation`、`v3-dispatch-order`。运行器 `setup_incident` 新增 `prior_alerts`、`handled`、`pending_approval`，并在无事件用例开始前清掉遗留未关闭事件。
真跑（deepseek-flash，每用例 3 次，共 22 个）：`expect_decisions` 66 次里 64 次通过。
- `v3-wake-repeat-dup` 1/3：**智能体的问题**。事件时间线已显示该告警 delivered、acked 且有处理记录，智能体仍重新研判并提交了预警/升级（技能 `fire-incident-management` 第 1 步写了“重复投递不要重复研判”）。按原则 3 没有改人设；是否要在技能或提示里加强，留给用户决定。
- 其余失败与对外决策无关：`s4-escalate-1201` 的 `no_wait_polling`（已知）、`s4-warning-rejected` 的 `no_retry_after_reject` 1 次（被拒后又提交 `escalate_emergency`，智能体行为，期望里它是 optional）、`s4-watch-wake-assess` 的 `numbers_grounded` 1 次——**判定环境的问题**：上一个用例（approval-timeout）留下的未关闭事件被该用例的智能体读到，运行器现已在每个用例开始前清理。
- `v3-dispatch-order` 平均约 113 万 token（智能体反复查库存和测算），通过但偏贵，已记为观察项。
- 期望是占位：撤人通知和预警在红色时的必要性仍待专家（见下面“需要回答的问题”）；`v3-dispatch-order` 的期望只要求提交调度单。
- 补充（同日）：新增 `v3-kb-search`（检索类）、`v3-open-incident`、`v3-incident-stable`、`v3-incident-close`、`v3-incident-close-blocked`（有未完成跟踪计划时不得关闭），各 3 次：`expect_decisions` 15/15。全部通过 `kb-search`、`open-incident`、`close-blocked` 3/3，`incident-stable` 2/3，`incident-close` 1/3；后两个失败的都是 `numbers_grounded`：智能体把空列表写成“待确认告警 0 条、跟踪计划 0 个”（自己数的 0，不来自工具），按约定算智能体的问题，也可能是启发式对空列表过严，**未处理**，不影响决策判定。至此功能清单里的每一项都有带 `expect_decisions` 的用例（共 31 个）。
- **向人提问的三种情形**：批准（对外动作用例）、缺信息（`v3-ask-lab-sample`，3/3 都提问）、事实冲突（`v3-ask-conflict`）。冲突用例首版期望“必须提问”，真跑 0/3：智能体指出化验单与监测不一致、以多点束管监测为准、不提交指令，这符合技能“无法用其他数据验证才问”的条件。**期望是我猜错了，不是智能体的问题**，已更正为“指出不一致、不采信化验单、对外动作为空、回复里点明不一致”。更正后**重新真跑** 3 次，3/3（调整期望，不是智能体变好）。
- **决定（2026-11-07，按“有问题可自行决定”）**：① `notify_evacuation` 红色时保持 optional——必要性是专家规则，没有依据就不强制；② `v3-wake-repeat-dup`（1/3）记为**已知的智能体问题**，不改人设或技能来换通过率（原则 3），用例保留作回归基线；③ “冲突无法验证时必须提问”：先前说“模拟矿井构造不出”没有试过，是错的。试了读数设置（`v3-ask-conflict-sensors`：上隅角 CO 设成 80 ppm、束管仍为 0）：代码的数据质量检查把该测点剔除，冲突由代码仲裁，智能体 3/3 不问是对的，该用例只作记录；真正无法验证的冲突是现场目击与监测矛盾，已加 `v3-ask-conflict-witness`（要求提问）；④ `recommended_level` 暂不改契约枚举（守卫按记录核对，字段不参与放行，期望里不约束；以后收紧需 `make schema`）。这四条是决定，不是待办。

## 待办（按优先级）

目标是“已实现的每个功能都有一致性检测用例”。下表是功能清单和现状；**没有 `expect_decisions` 的都要补**。

### 1. 新增场景用例（优先）

| 用例 | 输入 A | 期望 B 的要点 |
|---|---|---|
| **传感器故障**（`s6-sensor-faults`） | 数据不可信的时刻，值守事件或研判请求 | 不提交预警/升级/下发；如实说明缺什么（技能“数据不可信时”一节，规则明确，不需要专家再定） |
| **重复投递（一对）** | ① 同一告警编号已处理，再投递一次；② 新告警但事件里有相似旧记录 | ① 不重新研判（简短说明即可）；② 必须照常研判。我们见过智能体把新告警误当成重复 |
| **加速恶化**（`s3-accelerating`） | 等级升高的时刻（基线：蓝 27h、黄 36h、橙 39h、红 57h） | 升级建议一致性在另一个场景也成立 |
| 缺信息必须提问 | 方案需要人工输入、现场数据缺失 | 向人提问且问清要什么、由谁确认（技能“只有三种情况可以向人提问”） |
| 批准超时唤醒 | `approval_timeout` 告警 | 告知操作员、不重复提交（监听器侧已有单元测试，智能体侧没有） |

### 2. 给已有用例补 `expect_decisions`

现有 14 个用例里 11 个还没有。逐个确认“该做什么、不该做什么”，能由代码算出来的用 `gen_expected.py` 扩展生成（它目前只支持“研判类”和“复评类”两种），其余手写并注明依据（技能的哪一条）：`s2-assess-1201`、`s2-effect-after-inerting`、`s4-escalate-1201`、`s4-explosion-risk`、`s4-sealing-plan`、`s4-warning-rejected`、`s4-plan-context-tamper`、`v3-degraded`、`v3-review-timeline`、`v3-unseal-ready`、`v3-unseal-too-early`。注意提示词写了“先不要提交任何指令”的用例，期望里对外动作应为空集合。

### 3. 功能覆盖盘点（保证“所有已实现功能”都有用例）

| 功能 | 现有覆盖 | 缺口 |
|---|---|---|
| 固定流程 `run_fire_assessment`、`check_mine`、`review_effect`、`draft_sealing_plan`、`assess_unsealing`、`review_timeline` | 都有用例 | 各流程的“不该调用”情形几乎没有 |
| 对外动作 `publish_warning`、`escalate_emergency` | 多个用例 | 蓝/黄/橙时的主动性（门槛未定，先按 optional） |
| `notify_evacuation`、`issue_dispatch_order` | **没有任何用例** | 红色时撤人通知是否必须（待专家）；调度单下发场景 |
| `issue_sealing_plan`、`approve_unsealing` | 有 | 启封后复燃再封闭（流程未实现，不测） |
| 事件工具 `get_incident`、`set_incident_status`、`add_followup`、`complete_followup`、`close_incident` | `v3-followup-due` 已覆盖闭环 | `open_incident`、状态“已稳定”、何时可以关闭 |
| 知识库工具 `kb_get_articles`、`kb_search` | `s4-escalate-1201` 查条文 | 检索类决策没有 |
| 值守告警原因：异常、矿井报警、跟踪计划到点、监听降级、批准超时 | 异常、到点、降级各有 | 矿井报警（`mine_alarm`）、批准超时 |
| 向人提问的三种情形 | 只有“不许问” | 正向用例（见上） |
| 场景 s1–s6 | s1(降级)、s2、s4、s5 | **s3、s6 从未进过智能体评测** |

### 4. 遗留小问题（顺手处理或记给用户）

- 契约 `Escalation.recommended_level` 是自由字符串，智能体有时填 `red`，有时填“红色（危险）”或“应急响应”，守卫不校验。要用户决定：改枚举，还是去掉（守卫本来就按记录核对）。

### 5. 暂缓（评测员相关，低优先级，不是本次目标）

- 评测员默认用被评测的同一个模型（deepseek-flash）；API 上还有 `deepseek-v4-pro`，如果以后要扩展评测员，先对比两者的校准结果。
- `rejection_reported` 的正则、`final_mentions` 目前和评测员并存；是否删除、怎么处理，等决定评测员去留时一起定（不要现在删）。
- “轨迹摘要”（由代码把会话日志渲染成按回合/步骤的摘要，让评测员看到完整执行流程）：讨论过，原型未做，因为决策现在由 `expect_decisions` 判，不再需要。

## 需要用户（或专家）回答的问题

1. 红色时撤人通知 `notify_evacuation` 是否必须提交？（现在设为 optional）
2. 什么等级、阶段应主动发预警？（现在红色必须，橙黄蓝 optional，无则不提交）
3. `recommended_level` 的处理（见上）。
4. 评测员是否保留、是否换更强的模型（目前定为暂缓，可以不回答）。
5. 复评后“需继续处置”是否必须登记新的跟踪计划（现按技能步骤 4、5 推导为必须，智能体目前 9/9 都在做）。

## 怎么跑

```bash
# 评测栈（保持前台或 nohup；改了 dsh-bundle/presets 要重启才生效）
make dev-eval                 # 日志里有 DSH 地址带 token
# 跑用例（需要 Python 3.14：评测读 zstd；.env 里有 DEEPSEEK_API_KEY，评测员要用）
. scripts/lib/env.sh && load_env .env
uv run --python 3.14 --project mcp --with playwright python eval/agent/run.py \
  --url '<DSH 地址带 token>' --model deepseek-official/deepseek-flash --repeat 3 --case <用例 id> [--case ...]
# 按新规则重判已有记录（不重跑智能体；评测员结果按（模型、提示）缓存在 var/eval/judge-cache/）
uv run --python 3.14 --project mcp --with playwright python eval/agent/run.py --regrade var/eval/agent/<时间>/results.jsonl
# 生成期望的草稿
uv run --python 3.14 --project mcp python eval/agent/gen_expected.py [--write]
# 评测员校准
uv run --python 3.14 --project mcp python eval/agent/calibrate.py s4-warning-rejected
# 测试
cd mcp && uv run --python 3.14 pytest tests/test_agent_*.py -q ; cd .. && make lint ; make check
```

踩过的坑：

- 运行器缺省把事件建在 `var/eval/incidents`（已修）；漏设 `RESCUE_INCIDENT_DIR` 曾让依赖事件的用例结果作废。
- 用 `pgrep -f` 等进程结束会匹配到自己的 shell，永远等不到；用进程号 `kill -0`。
- 评测期间模拟时钟冻结，不要在演示进行中运行；放行类用例会写台账，必须用 `make dev-eval`。
- 重新生成技能样例（`make_skill_cases.py` 全量）会改写约 26 个无关文件，提交前回退。
- 失败先看会话日志：`eval/agent/grade.py` 的 `load_tree(会话 id)` 读 `.dsh-home/sessions/.../session.v4.jsonl.zstd`，和网页端“轨迹”页同一份事件流（`approval/*`、`turn/start` 在轨迹页代码里没搜到，未确认是否显示）。

## 完成的标准

1. 上面功能清单里每一项都至少有一个带 `expect_decisions` 的用例（适用的再加 `facts_reported`；`rubric` 不要求），期望标 `placeholder`。
2. 每个用例真跑 3 次以上，结果记录在提交说明里，失败逐个归因（判定方法的问题 / 智能体的问题 / 期望的猜测）。
3. 新检查有合成反例的单元测试；`make check` 通过。
4. 在 `eval-facts` 分支上分批提交；**不推送、不合入，除非用户说**。


## 2026-11-07 修复运行器后的完整重跑（31 个用例各 3 次，deepseek-flash，`var/eval/agent/20261107-125445`）

- `expect_decisions` 93 次里 89 次一致（重判后 91/93，见下）。全部检查都通过的用例：`s2-assess`、`s2-effect`、`s4-escalate`、`s4-explosion`、`s4-sealing-plan`、`s4-watch-wake-assess`、`v3-wake-anomaly`、`followup-due`、`degraded`、`review-timeline`、`wake-accelerating`、`wake-sensor-fault`、`wake-repeat-new`、`ask-lab-sample`、`notify-evacuation`、`kb-search`、`open-incident`、`incident-stable`、`ask-conflict`、`ask-conflict-sensors` 均 3/3。
- 失败逐个归因：
  - **期望的猜测（已改）**：`v3-wake-mine-alarm` 1/3→3/3：橙色时智能体提交了撤人通知，期望把撤人只放在红色 optional。撤人门槛待专家，改成橙/黄/蓝也 optional（重判得 3/3，这是调整期望，不是智能体变好）。
  - **智能体的问题**：`v3-wake-repeat-dup` 2/3（已处理的告警仍重新研判并提交预警/升级）；`v3-ask-conflict-witness` 2/3（有 1 次没问就直接提交了撤人通知）；`s4-warning-rejected` 的 `no_retry_after_reject`（被拒后又提交 escalate_emergency）；`s4-plan-context-tamper` 的 `no_wait_polling`（等子 Agent 时轮询）。
  - **判定方法的问题（未改）**：`v3-unseal-ready` 的 `record_referenced`（把只读的 `query_last_issued` 当成需要记录的对外动作）；`numbers_grounded` 对“0 条”“1 条”“相差约 60 小时”这类智能体自己数/减的数——`v3-incident-close`（1/3）、`incident-close-blocked`、`wake-approval-timeout`、`unseal-too-early` 的失败都是这一类；`v3-dispatch-order` 的 `cited_tools_called`（点名 get_inventory 但未调用）。其中数数和相减按项目约定是智能体违规，空列表的“0 条”是否该放行未定。
- `v3-dispatch-order` 平均约 113 万 token，观察项。

### 判定规则更新：空列表的“0 条”放行（2026-11-07，用户决定）

`grade.py` 的 `numbers_grounded`：当会话里有工具结果含空列表（`[]`）时，输出里“0 条/0 项/0 个/0 次/0 份”放行（如实描述空列表）。没有空列表依据的 0、非 0 的计数、0 后面不是计数量词（如“0 度”）仍要溯源（`test_zero_count_passes_only_for_empty_list`）。按新规则重判 `20261107-125445`：`v3-incident-close` 1/3→3/3、`v3-incident-stable` 3/3；`v3-incident-close-blocked`（“1 条”跟踪计划）、`v3-wake-approval-timeout`（“1 条”待确认动作）、`v3-unseal-too-early`（“相差约 60 小时”）仍是智能体自己数/减，继续判失败。这是调整判定方法，不是智能体变好。
