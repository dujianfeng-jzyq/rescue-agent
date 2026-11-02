---
name: firefighting-plan
description: 采空区自燃防灭火方案（第 1 阶段预警处置）的措施选择原则和结构化方案写法。
whenToUse: 需要为采空区自燃预警编写或修改防灭火方案时。
---

# 防灭火方案编写

> 状态：草稿，待防灭火专家审定。措施选择和用量以客户矿井防灭火设计为准。

## 措施与步骤类型

| 措施 | step.type | 常用参数（params） | 作用 |
| --- | --- | --- | --- |
| 注氮 | `inert_injection` | `rate_m3_h`、`gas`=N2 | 降低高温区氧浓度 |
| 注浆 | `grouting` | `volume_m3` | 覆盖遗煤、隔氧降温 |
| 喷洒阻化剂 | `inhibitor_spray` | `amount_t` | 抑制煤氧化 |
| 堵漏 | `leak_sealing` | `reduction`（预计减少漏风比例 0~1） | 减少向采空区漏风 |
| 均压 | `pressure_balancing` | `reduction` | 减小采空区两侧压差 |
| 监测 | `monitor` | — | 加密束管取样、观察效果 |

## 选择原则（待专家确认）

- 措施组合优于单一措施：注氮通常与堵漏或均压配合，否则漏风会把氮气稀释掉。
- 注氮量用 `dispatch.estimate_inert_gas` 估算，不要自己算；把估算假设写进 description。
- 采空区火灾保持通风系统稳定：**不要**写反风、风流短路、停主要通风机（第98条，红线规则会拦截）。
- 每个方案最后都要有 `monitor` 步骤，写明观测哪些测点、多久复核一次效果。取样和复核频次按本矿《自然发火预测预报制度》；没有依据时写“频次由指挥部确定”，不要自己定一个数。

## 结构化方案（Plan）

方案骨架由 workflow 的 `draft_plan`（kind = `firefighting`）生成，**不要从头手写**：

- 它给出 `snapshot_id`、`context`（甲烷、是否上升、氧浓度、爆炸危险性等，由代码取自研判快照和监测数据）和必备步骤 `leak_sealing → inert_injection → monitor`（顺序不能调换，可在中间插入步骤）。
- 注氮流量：给出漏风量 `leak_m3_min` 和目标氧浓度 `target_o2_pct` 时，`draft_plan` 用 dispatch 的公式估算并填进 `params.rate_m3_h`；没有就在“待补充”里写“需人工给出”，不要自己算。
- 你可以改：步骤说明、巷道、可选措施（`grouting`、`inhibitor_spray`、`pressure_balancing`，按上表）、`basis`、`title`。
- 不能改：`context`、`snapshot_id`、`panel`、`kind`、必备步骤。改了 actions 发布时会拒绝；需要人确认的现场条件用 `human_inputs`（写明确认人）重新调用 `draft_plan`。
- `basis` 每条都要来自 kb 检索结果或 `draft_plan` 带上的固定条款，照抄 document 和 article。
- 改完调用 `kb.check_plan_redlines`（tool = `issue_firefighting_plan`）自查，它同时核对方案与草案快照。
