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

```json
{
  "plan_id": "FP-<日期>-<序号>",
  "kind": "firefighting",
  "title": "1201 采空区自燃防灭火方案",
  "context": {
    "fire_type": "goaf_spontaneous_combustion",
    "as_of": "<get_clock 的时间>",
    "methane_pct": "<get_latest 的 CH4 值>", "methane_rising": "<gas_trend 判断，未知填 null>", "o2_pct": null,
    "explosion_risk": "<assess_explosion_risk 的结果>"
  },
  "steps": [
    {"order": 1, "type": "leak_sealing", "description": "...", "roadways": ["R-1201-RET"], "params": {"reduction": "<预计减少漏风比例，无依据时不填>"}},
    {"order": 2, "type": "inert_injection", "description": "...", "params": {"rate_m3_h": "<estimate_inert_gas 的结果>"}},
    {"order": 3, "type": "monitor", "description": "按本矿预测预报制度的频次取束管气样，重点看 BT-1201-G1/G2 的 CO 和乙烯"}
  ],
  "basis": [{"document": "<kb 返回的 document>", "article": "<条号>", "source": "ragflow"}]
}
```

- 示例里尖括号是占位，写方案时换成对应工具的实际返回值（数值写成数字）。`context` 只填工具给出的值；没有的填 `null`。红线规则对未知值按“不放行”处理，所以与进入灾区相关的条件（温度、甲烷）务必先查清。
- `basis` 每条都要来自 kb 检索结果，照抄 document 和 article。
- 写完调用 `kb.check_plan_redlines`（tool = `issue_firefighting_plan`）自查。
