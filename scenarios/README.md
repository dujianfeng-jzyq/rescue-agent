# 标准场景

模拟矿井的 6 个标准场景，同时用作演示脚本、评测集和回归测试集（见实施方案“模拟矿井设计”一节）。

| 文件 | 场景 | 主要检验 |
| --- | --- | --- |
| `s1-normal.yaml` | 正常回采 | 误报率 |
| `s2-slow-oxidation.yaml` | 缓慢氧化，及时处置后受控 | 预警分级、防灭火方案、效果评估（一期闭环首选） |
| `s3-accelerating.yaml` | 加速自燃，处置有效 | 预警时效、措施组合 |
| `s4-escalation.yaml` | 处置无效，恶化为火灾 | 升级、撤人名单、封闭方案、红线拦截 |
| `s5-sealed-fire-area.yaml` | 封闭火区演化与启封（含复燃） | 火区管理、启封条件、复燃报警 |
| `s6-sensor-faults.yaml` | 传感器故障与误报 | 数据质量检测 |

## 字段

- `start`、`duration_h`：模拟起止时间。
- `speed`：时间加速倍数（模拟秒/真实秒）。演示时可通过 `/sim/speed` 调整。
- `params`：过程模型参数，字段见 `mcp/mine-data-sim/src/rescue_mine_data_sim/engine.py` 的 `ProcessParams`。
- `script`：剧本事件（与处置无关的外部变化，如漏风突增、停采）。`type` 取值见 `SimEngine._apply`。
- `faults`：传感器故障。`kind` 取 `drift`（漂移，magnitude 为每小时偏移量）、`stuck`（卡值）、`dropout`（断线）、`spike`（尖峰，magnitude 为倍数，持续 1 小时）。

## 参数可信度

所有参数都是为了让趋势“看起来合理、能响应处置”而手工调出来的，**不代表任何真实煤层**。
阶段 1 需由防灭火专家依据程序升温实验数据和文献案例校准，并在每个场景文件里注明数据来源。
