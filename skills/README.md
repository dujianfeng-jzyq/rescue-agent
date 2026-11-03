# 技能规范（第二版）

技能是专家经验和算法的唯一来源（ADR 0007，`docs/design-v2.md` 4.2）。同一个技能目录有两个使用方：模型读 SKILL.md、在沙箱里运行脚本做参考计算；证据插件在服务端运行同一批脚本，结果存为记录，可以被指令引用。

> 过渡期：`skills/<角色>/<技能>/` 是第一版的技能草稿（只有 SKILL.md），P1–P4 逐个并入下面格式的新技能后删除。判断一个目录是不是第二版技能：根目录下有 `skill.yaml`。

## 目录

```
skills/<技能名>/
  SKILL.md          # frontmatter：name、description、whenToUse、version；正文：何时用、步骤、解读要点、边界
  skill.yaml        # 脚本登记：只有登记过的脚本能被证据插件运行
  scripts/          # 算法脚本，以及同目录下的辅助模块（以 _ 开头）
  templates/        # 报告模板（Markdown + 占位符）
  references/       # 参数（yaml）、方法说明、规程摘录
  tests/cases/<脚本名>/<样例名>.input.json
  tests/cases/<脚本名>/<样例名>.expected.json
```

技能名用小写字母、数字和连字符，与 frontmatter 的 `name` 相同。

## SKILL.md

```markdown
---
name: explosion-triangle
description: 火区混合气体爆炸危险性（多组分、温度补偿的爆炸三角形）
whenToUse: 判断密闭内或采空区气体是否有爆炸危险，或解释风险图时
version: 0.1.0
---
```

- 正文写给模型和专家读：何时用、步骤、怎么解读结果、边界。**不写阈值和参数**（参数在 `references/`，结果由脚本给出），不让模型估算（CLAUDE.md 约定 1）。
- `version` 按语义化版本递增，记录里会写下技能版本。

## skill.yaml

```yaml
scripts:
  explosion_triangle:                 # 脚本名：证据插件和样例目录按它引用
    file: scripts/explosion_triangle.py
    description: 多组分、温度补偿的爆炸三角形，输出分区和风险图
    timeout_s: 10                     # 可选，缺省 30
    svg: true                         # 可选：运行时传 --svg-out，输出一张图
```

## 脚本约定

1. **纯函数，文件进、文件出**：

   ```bash
   python -E -s -B -X utf8 scripts/<名>.py --input-file <输入.json> --output-file <结果.json> [--svg-out <图.svg>]
   ```

   不访问网络、数据库，不读环境变量里的凭据，不写输出文件以外的任何地方。参数从本技能的 `references/` 读（按脚本所在目录定位，不依赖当前目录）。
2. **输出可审计**：结果是一个 JSON 对象，必须有：
   - `algorithm_version`：算法版本（字符串）；
   - `params`：本次实际使用的参数；
   - `warnings`：全部警告（列表，可为空）；
   - `result`：计算结果。数据缺失时明确输出“无法判定”及原因，不估算。
3. **只依赖** Python 标准库、`requirements.txt` 里列出的包和本目录下的辅助模块。自检会扫描 import，用了别的包就失败。DSH 镜像按 `requirements.txt` 安装。
4. **脚本是受信代码**：证据插件起的子进程不在沙箱里（验证清单 #22），只经评审和 CI 进入技能库；部署时技能目录只读挂载。
5. **输入错误**以非零退出码结束，原因写到 stderr（证据插件和运行器会把它作为“非零退出”报告）。

运行时的环境：只传 `PATH`、`LANG`、`LC_ALL`、`TZ`；`-E` 忽略 `PYTHON*` 环境变量，`-B` 不写 `__pycache__`（技能目录只读），`-X utf8` 统一编码；工作目录是本次运行的临时目录；超时先 SIGTERM，1 秒后 SIGKILL。Python 侧的实现在 `rescue_contract.skills`，TS 侧在证据插件的 `runScript`，两边规则相同。

## references 里的参数

参数文件（`references/*.yaml`）顶层必须有：

```yaml
status: placeholder        # placeholder（开发占位值）或 reviewed（专家审定）
reviewed_by: null          # 审定人和日期，status 为 reviewed 时必填
```

改参数必须同时更新受影响的样例；提交说明写明是占位值还是专家审定值（CLAUDE.md 约定 6）。

## 采集清单（references/collect.yaml）

需要读矿井数据的技能（如 `fire-assessment`）在这里声明“采什么”；证据插件按清单从 mine_data 取数，存为证据记录，再把证据交给脚本。脚本自己不连矿井（它没有令牌，也不该访问网络）。

```yaml
status: placeholder
reviewed_by: null
kinds:                          # 一份清单可以有多种采集，如 check（风险检测）、assessment（完整研判）
  check:
    sensors:                    # 测点组
      - name: tubes             # 证据里 groups.<name>
        select: {source: bundle_tube, panel: true}   # 按来源、按工作面编号（测点 id 含工作面），或 ids: ["SM-{panel}-UC"]
        require_measurands: [O2, CO2]                # 可选：测点必须同时具备的参数
        measurands: [CO, O2, CH4, C2H4, C2H2, H2, CO2, N2]   # 可选：只取这些参数（缺省为测点的全部参数）
        window_h: 6             # 可选：历史窗口（矿井小时）；缺省只取最新值
        required: true          # 没有匹配的测点时写进证据的 missing
    events_h: 6                 # 可选：安全监控事件
    fire_areas: true            # 可选：火区台账；还有 personnel、inventory、rescue_teams、network
```

证据内容：`{kind, panel, as_of, groups: {<name>: {sensors, measurands, latest, readings, window_h}}, missing, events?, fire_areas?, …}`。测点按 id、读数按（测点、参数、时间）排序，时间截到秒。只能用 mine_data 的工具（CLAUDE.md 约定 5）。证据插件（TS）和 Python 采集小工具（`python -m rescue_contract.collect`，不经 DSH 的基线评测用）实现同一规则，一致性测试保证两边采到的证据逐字节相同。

## 自检样例

- 每个登记的脚本至少一组样例：`tests/cases/<脚本名>/<样例名>.input.json` 和同名 `.expected.json`；
- 自检用上面的方式运行脚本，输出与 `expected` **整体相等**才算通过（浮点数也逐位相等：脚本是确定性的）；
- 登记了 `svg: true` 的脚本，自检还检查生成了非空的 SVG；
- 运行：`make skills-check`（`make check` 包含它；镜像构建时也运行）。

## 报告模板（templates/）

`templates/<名>.md` 是 Markdown + 占位符（Mustache 的一个子集），证据插件的 `renderReport` 用它和记录生成报告事实部分，存为报告记录：

- `{{a.b}}` 取值：第一段从内到外在上下文里找，`{{.}}` 是当前项。**键不存在时报错**（模板写错了不悄悄留空）。null 写“缺失”，true/false 写“是/否”，数字按 ECMAScript 写法原样输出（不取整），对象和列表不能直接输出。不做 HTML 转义。
- `{{#a}}…{{/a}}`：列表逐项、对象压入上下文、其他真值渲染一次；null、false、空字符串、空列表不渲染（0 是真值）。`{{^a}}…{{/a}}` 反之。
- `{{! 注释 }}` 不输出；只有一个区块标签或注释的行整行去掉，模板可以按行排版。
- 模板拿到的数据：`{panel, as_of, as_of_text, record_ids: {名: 编号}, <名>: 记录内容, …}`（`rescue_contract.template.report_data`），外加流程给的少量非记录数据（如 `chart_markdown`）。
- 模板只排版，不计算：要合计、计数、拼接的内容由脚本输出（如 `persons_text`、`data_quality_all`）。
- 样例：`tests/templates/<名>/<样例>.input.json`（渲染数据）+ 同名 `.expected.md`，每个模板至少一组；`make skills-check` 和 TS 测试各渲染一遍，两边结果必须逐字节相同。

Python 实现在 `rescue_contract.template`，TS 在证据插件的 `src/evidence/template.ts`。

## 样例的生成

火灾闭环的三个技能（fire-assessment、explosion-triangle、emergency-escalation）的样例由 `scripts/make_skill_cases.py` 从模拟矿井的标准场景生成；改了参数或算法后用 `--expected` 只重写期望，并在提交说明里写明。关键字段与第一版服务的一致性由 `mcp/workflow/tests/test_skill_parity.py` 保证。跨技能复制的辅助模块（`_quality.py`、`_evidence.py`、`_skill.py`）有一致性测试，两份一起改。注意辅助模块不要用与标准库内建模块同名的名字（如 `_io`）。
