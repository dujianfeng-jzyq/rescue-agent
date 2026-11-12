# 待专家审定清单

阈值、判据、参数、选项表目前都是**开发占位值**（CLAUDE.md 约定 6）。这里把要请通风、防灭火专家审定的事项集中列出，便于评审时逐条过。
每条写：现在怎么做、参数在哪里、要专家回答什么。专家给出结论后，改对应文件的取值、把 `status: placeholder` 改为已审定并填 `reviewed_by`，再在本表标为“已审定”。

2026-11-12 建立，汇总自各技能 `references/`、SKILL.md 和 `docs/progress-log.md` 的遗留项。

## 一、研判与预警

| # | 事项 | 现在的做法（占位） | 在哪里 | 请专家回答 |
| --- | --- | --- | --- | --- |
| 1 | **定级回差**（2026-11-12 新增） | 等级要降时，上一次那一级的任一指标仍不低于“阈值 ×（1 − 0.2）”就保持；全部回落到解除值以下才降。升级不受影响。起因：封闭注惰后乙烯被稀释（2.07 → 1.82，红色阈值 2.0），等级红→橙，数小时后积聚回 2.10 又升红 | `skills/fire-assessment/references/thresholds.yaml` 的 `hysteresis.band` | ① 回差取多少，是否按指标分别定（乙烯、束管 CO、格雷哈姆系数的波动性不同）；② 是否还要加时间条件（如解除值以下保持 N 小时才降）；③ 指标读不到数（如封闭后测点断电）时是否也应保持上一次的等级（现在不保持）；④ 封闭、注惰期间的稀释是否应单独处理 |
| 2 | 预警分级阈值 | 蓝、黄、橙、红四级，按上隅角 CO、束管 CO 及上升速率、乙烯、乙炔、格雷哈姆系数，满足任一条件即触发 | `skills/fire-assessment/references/thresholds.yaml` 的 `levels` | 按本矿《自然发火预测预报制度》和煤层标志气体临界值给出各级阈值 |
| 3 | 自燃发展阶段 | 乙炔检出为激烈氧化、乙烯检出为加速氧化、束管 CO 偏高或上升为缓慢氧化 | 同上 `stage` | 按本矿煤样程序升温实验的标志气体出现条件替换 |
| 4 | 风险检测与监听判据 | 必需测点、数据中断时长、异常的定义、确认和恢复次数 | `skills/fire-assessment/references/watch.yaml` | 按本矿采样制度和值班要求定 |
| 5 | 封闭后哪些测点停用 | 封闭后工作面的上隅角、回风巷安全监控测点视为在密闭内断电，不参与分级、不算必需 | `watch.yaml` 的 `sealed_exempt`；`_warning.py` 的 `SEALED_NOTE` | 按本矿封闭范围定 |
| 6 | 实时红线报警规则 | 规则表 | `skills/fire-assessment/references/alarms.yaml` | 审定规则和限值 |
| 7 | 数据质量判据 | 尖峰倍数、卡值点数、交叉校核容差写在脚本里 | `skills/fire-assessment/scripts/_quality.py`（待挪进 `references/`） | 审定取值 |
| 8 | 爆炸三角形 | 顶点坐标、温度修正、N₂/O₂ 当量比、分区判据、多测点汇总 | `skills/explosion-triangle/references/` | 审定参数和汇总规则 |
| 9 | 受威胁人员 | 计算条件与参数 | `skills/fire-assessment/references/personnel.yaml` | 审定 |

## 二、处置、复评与跟踪

| # | 事项 | 现在的做法（占位） | 在哪里 | 请专家回答 |
| --- | --- | --- | --- | --- |
| 10 | 复评观测期 | 处置后至少观测 12 小时才下“已受控 / 需继续处置”的结论；下次复评 = 现在 + 12 小时 | `skills/effect-review/references/effect.yaml` 的 `min_observe_h`、`review_interval_h` | 观测周期取多长 |
| 11 | **复评提前判恶化**（2026-11-12 新增） | 新出现标志气体随时判恶化；观测满 2 小时、速率 ≥ 处置前 × 1.5 且加快不少于 CO 1 ppm/h、温度 0.5 ℃/h 也判恶化 | `effect.yaml` 的 `early_worsening` | 倍数、加快量下限、最短观测时长；还有哪些指标要列入 |
| 12 | 复评判据 | 停止上升、减缓比例 0.5、速率“不变”的下限 | `effect.yaml` 的 `controlled_rate`、`slowdown_ratio`、`flat_rate` | 审定 |
| 13 | 复评间隔 | 处置后 2 小时复评；按等级的间隔上限 蓝 8、黄 8、橙 6、红 1 小时（为方便演示取得偏长） | `skills/fire-incident-management/references/followup.yaml` | 按本矿制度给出实际值 |
| 14 | 按预警等级的处理（分级决策表） | 各级做什么、谁来做 | `skills/fire-incident-management/SKILL.md` “按预警等级处理” | 按本矿火灾专项应急预案审定 |
| 15 | 橙色处置选项 | 注氮（建议）、注浆、堵漏，可多选；注氮可以不做（2026-11-11 用户决定） | `mcp/contract/src/rescue_contract/response_options.py` | 选项是否完整，注氮能否不做 |
| 16 | 撤人门槛 | 红色必撤；橙、黄、蓝时由智能体按研判决定是否撤人 | `fire-incident-management/SKILL.md`；`docs/eval-consistency.md` | 各级是否撤人、撤到哪里 |
| 17 | 升级判据 | 达到红色等条件建议升级为应急响应；“建议启动几级响应”还没做 | `skills/emergency-escalation/references/escalation.yaml` | 按预案定升级条件和响应级别 |

## 三、封闭与启封

| # | 事项 | 现在的做法（占位） | 在哪里 | 请专家回答 |
| --- | --- | --- | --- | --- |
| 18 | 建密闭步骤的红线 | 温度、能见度只对“进入灾区”设红线（能见度未知按不通过） | `mcp/contract/src/rescue_contract/redlines.py` | 建临时密闭要派人到密闭位置作业，温度、能见度是否也应对该步骤设红线 |
| 19 | 注氮流量 | 漏风量和目标氧浓度没有输入途径，注氮流量需现场确定 | `skills/sealing-plan/scripts/skeleton.py`、`skills/firefighting-plan/references/plan.yaml` | 注氮流量怎么定，需要哪些输入 |
| 20 | 封闭方案与物资 | 方案步骤、物资定额 | `skills/sealing-plan/references/`、`skills/firefighting-plan/references/` | 审定 |
| 21 | 启封条件 | 封闭时长、氧浓度、CO 及上升速率、乙烯、温度逐项判定；读数过旧 2 小时按无法判定 | `skills/unsealing-plan/references/readiness.yaml` | 审定条件和步骤 |

## 四、其他

| # | 事项 | 现在的做法（占位） | 在哪里 | 请专家回答 |
| --- | --- | --- | --- | --- |
| 22 | 动作分级、确认人与批准超时 | C 级弹确认，确认人和超时为占位 | `mcp/contract/src/rescue_contract/action_policy.py` | 各动作的确认人、超时和超时后升级对象 |
| 23 | 地图记录时效 | 记录过期 2 小时变灰、6 小时清掉 | `docs/design-v4-evacuation.md` 第 4 条 | 时效取多少 |
| 24 | 回风路线（现场核实） | 演示用的示意路线，不是真实风流 | `var/map/mine_map.yaml`（不进仓库） | 按真实通风系统核实 |
