---
name: sealing-and-unsealing
description: 采空区火区封闭方案和启封方案的步骤顺序与必备环节（对应规程第84—87、164条）。
whenToUse: 需要编写封闭火区方案（第 4 阶段）或启封方案（第 5 阶段）时。
---

# 封闭与启封方案

> 状态：草稿，待救护队带队指挥员和通风部门审定。封闭范围、密闭位置以现场侦察为准。

## 封闭方案（kind = sealing，发布工具 issue_sealing_plan）

必备顺序（红线规则会逐项检查）：

1. `evacuate`：撤出受影响区域人员（写明巷道）。
2. `inert_injection`：**封闭有爆炸危险或爆炸危险不明的火区，先注惰抑爆**（第85条）。爆炸危险性用 `fire_model.assess_explosion_risk`（按束管测点）判定，把它返回的 `explosion_risk` 原样填入 `context.explosion_risk`；II 区、III 区对应 `may_become_explosive`，同样要先注惰。
3. `temporary_seal`：先建临时密闭（第84条）。
4. `monitor`：观测风向、风量、烟雾和气体，确认无爆炸危险。
5. `permanent_seal`：再建永久或防爆密闭（第84条）。

其他要求：
- 多条巷道需要封闭时先支巷、后主巷；进、回风巷同时封闭时统一指挥、同时封堵（第85条），在 description 里写明。
- 密闭位置选在围岩稳定、断面较小、距巷道交叉口不小于 10 米的地点（第86条），在 description 里写明。
- 涉及人员进入灾区的步骤（`enter_disaster_area`）必须填 `team_size`（≥6）和 `standby_team: true`（第57、53条）。
- 封闭过程中可燃气体浓度迅速增加时所有人员立即撤离（第85条）——写进 monitor 步骤的 description。

## 封闭后

- 进入检查或加固密闭（`inspect_sealed_area`）应在封闭 24 小时后（第87条），`context.sealed_at` 必须填写，照抄 `mine_data.get_fire_areas` 返回的火区 `sealed_at`，台账里没有该火区时写明“需人工判断”，不要自行推算。
- 密闭被爆炸破坏时严禁派救护队探察或恢复密闭（第87条）——这种情况不要写进入步骤，交人工决策。

## 启封方案（kind = unsealing，发布工具 approve_unsealing）

必备顺序（第164条）：

1. `monitor`：启封前检查火区温度、各种气体浓度和巷道支护。
2. `power_cutoff`：切断回风流电源。
3. `evacuate`：撤出回风侧人员，在通往回风道交叉口设栅栏和警示标志。
4. `unseal`：锁风、逐段恢复通风。
5. `monitor`：启封后 3 日内每班检查，发现复燃征兆立即重新封闭。

启封条件（火区是否已熄灭）由火区状态监测和专家判断，本技能不给判据；`fire_model.check_realtime_alarms` 报复燃征兆时，不得建议启封。

写完调用 `kb.check_plan_redlines` 自查（tool 分别为 `issue_sealing_plan`、`approve_unsealing`）。
