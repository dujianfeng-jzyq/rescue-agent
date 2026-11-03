---
name: explosion-triangle
description: 火区混合气体爆炸危险性：多组分（含 CO、乙烯、H₂ 等）、温度补偿的爆炸三角形，给出分区（I–IV）和风险图。
whenToUse: 判断密闭内或采空区气体是否有爆炸危险、解释研判报告里的爆炸三角形和风险图、人员给出人工取样化验单要求判定时。
version: 0.1.0
---

# 火区混合气体爆炸三角形

> 状态：草稿，待专家审定。顶点坐标、温度修正系数、N₂/O₂ 当量比、分区判据和多测点汇总规则都是开发占位值（`references/`）。
> 以上一版 `explosion-triangle-synthesis` 的结构为基础，算法与第一版 `fire_model/explosion.py`（含 H₂、重新设计的分区判据）一致。

## 什么时候用

- 完整研判（`run_fire_assessment`）已经逐个束管测点算过，并在报告里给出总体结论和最不利测点的风险图：直接照抄报告。
- 人员给出**人工取样化验单**、或要细看某一个测点时，在沙箱里运行脚本试算（结果只作参考，注明“试算，仅供参考”）。

## 分区的含义

| 分区 | 名称 | 含义 |
| --- | --- | --- |
| I | 爆炸危险区 | 当前组成处于爆炸范围 |
| II | 减风危险区 | 可燃气体继续积聚（如减风、停风）会进入爆炸范围 |
| III | 增风危险区 | 混入空气（如增风、启封、密闭漏风）会进入爆炸范围 |
| IV | 相对安全区 | 当前组成下单纯混入空气或可燃气体积聚都不会进入爆炸范围；条件变化后须重新计算 |

- II、III 区**不是安全**，要照实说明哪种变化会进入爆炸范围。
- 分区、`explosion_risk`、`meaning`、`warnings` 照抄结果，不要自己看图判断分区。方案的 `context.explosion_risk` 照抄 `explosion_risk`（或总体的 `overall`）。
- 多个测点时总体结论取最不利；任一测点算不出来时总体为“无法判定”，按有爆炸危险对待。
- 没有 N₂ 实测值时 S 点按 O₂ 当量合成，注氮惰化时结论偏保守；化验单缺少的组分未计入，实际存在时结论偏乐观——结果的 warnings 会写明，照实转述。

## 脚本

`scripts/explosion_triangle.py`，输入二选一：

- 按工作面或测点：`{"evidence": <证据记录的 content>, "options": {"sensor_id": "BT-1201-G1"}}`（不给 sensor_id 时逐个束管测点）；
- 人工化验单：`{"sample": {"o2_pct": 8.2, "co2_pct": 3.1, "temperature_c": 32, "ch4_pct": 4.5, "co_ppm": 120}}`。
  CH₄、O₂、CO₂、N₂ 为 %，CO、C₂H₄、C₂H₂、C₂H₆、H₂ 为 ppm；O₂、CO₂ 和温度必须是实测值，缺少时先向人员索取，不要填估计值。

```bash
python -E -s -B -X utf8 <技能目录>/scripts/explosion_triangle.py --input-file input.json --output-file out.json --svg-out chart.svg
```

## 边界

结果用于风险研判，不替代现场检测和指挥决策。温度修正公式统一用于全部可燃组分是工程外推，没有逐种气体的试验验证。
