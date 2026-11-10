# 评测

两层评测，回答两个不同的问题：

| 层 | 回答 | 状态 |
| --- | --- | --- |
| 确定性基线 `baseline.py` | 计算模型（预警分级、数据质量、复燃报警）在标准场景上是否按期望工作 | 可用 |
| 智能体端到端 `agent/` | 智能体的执行流程、决策、下发的指令是否正确，数值是否有依据，是否守住红线（40 个用例，方案、用例清单和结果见 `docs/eval-consistency.md`） | 可用 |

## 确定性基线

```bash
make eval                                       # 全部场景
uv run --project mcp python eval/baseline.py s6 # 指定场景
```

逐小时推进模拟时钟（不经过大模型），记录每个预警等级的首次到达时间、最高等级、复燃报警时间，和 `expectations.yaml` 对照。明细写入 `var/eval/baseline.json`（含高温点真实温度，便于画“真实温度 vs 预警等级”的图）。

**期望值是开发占位**——阈值和场景参数都还没审定，目前只用于防回归。专家审定阈值后，应同步审定期望。

已发现并修复的问题：s6 中上隅角 CO 漂移 +7 ppm 未被交叉校核识别、误报蓝色预警（数据质量交叉校核的容差由 2.5 倍收紧为 1.5 倍，现在在 `skills/fire-assessment` 的脚本里）。

## 智能体端到端

```bash
make dev-eval                                    # 另开终端；评测环境（指令台账、记录、事件库、审计写到 var/eval/，不启动监听器）；记下打印的带 token 地址
make eval-agent ARGS="--url 'http://127.0.0.1:3080/?token=…' \
    --model deepseek-official/deepseek-flash --repeat 3 [--case <用例 id>]"
uv run --project mcp python eval/agent/grade.py <会话 id>   # 对任意已有会话离线判定
```

- 用例在 `agent/cases.yaml`：场景 + 模拟时刻 + 提问（或值守事件）+ 期望决策 + 判定规则。可用 `apply` 在指定时刻施加处置（格式同 `expectations.yaml`）。运行时把模拟矿井重置为手动时钟并快进到该时刻，评测期间时钟不走。
- 用例字段（常用）：`incident`（在事件库建事件和告警，`handled: true` 表示这条告警已处理过）、`wake: true`（提示词由唤醒插件的消息生成器按事件库条目生成，与生产同源，不手抄）、`approvals`（人工确认时放行哪些指令，其余拒绝）、`operator_cards: true`（运行器扮演操作员回答代码给出的卡片：处置选项多选、封闭方案现场条件 6 题、点跳过；会弹这类卡片的用例必须写，否则运行停在卡片处）、`answers`（智能体提问时按序回答）、`advance_h`（第一轮结束后推进时钟、等后台任务唤醒）。
- `agent/run.py` 经 DSH 网页端提问（Playwright 无头浏览器）。DSH 0.2.1-rc.4 的 headless 和 ACP 入口不支持 Agent 预设，只能这样驱动。`--model` 取网页模型下拉里的 `提供方/模型 id`。
- `agent/grade.py` 读 `.dsh-home/sessions` 下的会话日志判定（规则全文见文件头）：委派、未调用/调用了 actions、指令引用记录（`record_referenced`）、期望决策（`expect_decisions`，见 `docs/eval-consistency.md`）、输出的数值能在材料里找到（`numbers_grounded`）、点名的工具确实调用过、调用了指定工具（`tools_called`）、不许多余提问（`no_questions`，代码给出的卡片——header 为“处置选项”“现场条件”——不计）等。
- 每个用例重复多次，按“通过次数/有效运行次数”汇总；明细写 `var/eval/agent/<时间>/results.jsonl`。
- 运行结束方式：`settled`（全部回合结束）、`asked_user`（停在 ask_user_question 等人回答；没写 `operator_cards`/`answers` 时评测不代答，按已有输出判定）、`timeout`、`llm_error`（模型调用失败）。后两种不计入通过率。
- 判定规则改了以后，`run.py --regrade var/eval/agent/<时间>/results.jsonl` 按新规则重新判定已保存的运行，不重新提问。
- 评测需要 Python 3.14（读 zstd 会话日志，`uv run` 会自动选）；`make dev-eval` 和 Docker 演示不要同时跑；偶发“没有找到评测会话”是基础设施抖动，重跑即可。

演示脚本（也用同一套运行器）：

- `agent/story.py`：整条故事线（黄 → 橙 → 红 → 封闭 → 复评 → 启封 → 复盘 → 收尾，14 项检查），按预警等级分段推进；橙色卡片默认点跳过（才会自然升红），`STORY_ORANGE_OPTIONS=注浆|堵漏` 则受控。
- `agent/demo_run.py`：点一次“开始演示”后扮演操作员，由演示导演推进（第五版），判定看导演记录和事件时间线。`STORY_ORANGE_OPTIONS` 同上，缺省 `注浆|堵漏`（回落分支）。
- `agent/edge.py`：值班会话边界情况（Docker）。

数值溯源的局限（启发式，结论要人工看明细）：

- 按报告写的小数位四舍五入匹配；报告写整数而材料里只有能四舍五入到它的小数（如 43.998 → 44）时只作“整数近似”提醒，因为小整数很容易碰巧对上。
- Agent 自己计数、求差、换算出的数（人数合计、时间跨度、温差）也判为不通过——按约定这些应来自代码。
- 子 Agent 照抄指挥 Agent 转述的旧数据不会被发现（转述内容算作它看到的材料）。
- 编号、日期时刻、条号节号、列表序号会先剔除；剔除规则漏掉的格式会产生误报。

覆盖缺口和后续见 `docs/eval-consistency.md` 第 8 节。
