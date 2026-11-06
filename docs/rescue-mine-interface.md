# rescue-mine 接口说明

> 接口版本 **1.0.0**，数据契约 **0.11.0**。**本文件由代码生成**（`make interface-doc`），不要手改；
> 测试会检查它与代码一致。设计依据见 `docs/design-v3.md` 第 4 节。

rescue-mine 是 rescue-agent 与矿井之间的**唯一接口**，也是**唯一随矿井变化的部分**：开发阶段由模拟矿井（mine-sim）实现，
产品阶段由真实矿井适配器实现。对接新矿井只需要实现本接口，rescue-agent 的技能、插件和判据不改。

## 边界规则

- rescue-mine **只有“读数据”和“执行动作”，不含策略**：不做红线校验、不做研判、不存指令台账。这些都在 rescue-agent 一侧（guard）。
- 写入类工具（见下）只有 guard 调用；智能体读矿井只经证据插件（留记录），写矿井只经 guard。
- 模拟器的真值、读数设置、倍速、事故注入是 mine-sim 的控制接口（`/sim/`），**不属于本接口**，智能体看不到。
- 所有时间是矿井本地时间（`MineTime`，ISO 8601，不带时区）；“现在”以 `get_clock` 为准，不用系统时间。
- 输入错误必须以工具错误返回并写明原因（SDK 里抛 `ToolError`）；数据质量为 `missing`、`suspect` 的读数不能当依据。

## 认证

Streamable HTTP + 静态 Bearer 令牌。读取工具认 `RESCUE_MCP_MINE_DATA_TOKEN`（没有则 `RESCUE_MCP_TOKEN`）；
**写入类工具**另认写入令牌 `RESCUE_MCP_MINE_ACT_TOKEN`（只有 guard 持有，读令牌调用写入工具返回 401）。
写入令牌也可以做普通读取（guard 要回读回执）。适配器应实现同样的读写分离。

## 工具总览

“功能”列为空表示**必需**；否则是可选功能，矿井不提供时对应工具可以不实现，由 `get_capabilities` 如实说明。

| 分组 | 工具 | 读/写 | 功能 | 说明 |
| --- | --- | --- | --- | --- |
| 自描述 | `get_capabilities` | 读 |  | 接口版本、本矿实际提供的工具和可选功能 |
| 自描述 | `describe_mine` | 读 |  | 工作面拓扑（进回风巷、采空区、避难硐室）和测点用到的语义角色 |
| 时间 | `get_clock` | 读 |  | 矿井时钟（模拟加速时即模拟时间）；研判一律以此为“现在” |
| 监测数据 | `list_sensors` | 读 |  | 全部测点：来源、可测参数、所在巷道、所属工作面、语义角色 |
| 监测数据 | `get_latest` | 读 |  | 各测点最新读数（含数据质量标记） |
| 监测数据 | `get_readings` | 读 |  | 测点历史读数（时间窗、可按参数筛选） |
| 监测数据 | `get_events` | 读 |  | 矿井监控系统自身产生的事件：超报警值、测点故障 |
| 监测数据 | `get_fire_areas` | 读 |  | 火区管理台账：封闭过的火区、状态、封闭和启封时间 |
| 通风网络 | `get_network` | 读 |  | 通风网络：节点（含井口、避难硐室）和巷道 |
| 人员物资 | `get_personnel` | 读 |  | 井下人员实时位置，含总数、班组、巷道汇总 |
| 人员物资 | `get_inventory` | 读 |  | 应急物资台账 |
| 人员物资 | `get_rescue_teams` | 读 |  | 救援力量台账 |
| 矿井自带报警 | `get_alarms` | 读 | `alarms` | 矿井报警平台的报警（拉取，含确认状态） |
| 设备 | `get_equipment_status` | 读 | `equipment` | 风机、水泵、制氮、注浆设备的运行状态 |
| 通知与指令 | `send_notice` | **写** | `notice` | 发送预警、撤人等通知，返回回执 |
| 通知与指令 | `dispatch_order` | **写** | `dispatch` | 下发物资、人员调度，返回回执 |
| 通知与指令 | `submit_treatment` | **写** | `treatment` | 提交处置动作（注惰、灌浆、封闭等），返回回执 |
| 通知与指令 | `get_action_status` | 读 | 任一通知/指令功能 | 按回执编号查询通知、指令的执行状态（异步生效）；提供任一通知/指令功能时必需 |

## 语义角色词表

技能按**角色**取数，不按测点编号：`list_sensors` 里每个测点带 `role`（和所属工作面 `panel`），采集清单写 `select: {role: upper_corner, panel: true}`。
对接新矿井时，适配器把该矿的测点映射到下面的角色；词表里没有的先用 `other`，并提给我们补充。
**词表是占位的**（演示后由专家补全）。

| 角色 | 含义 |
| --- | --- |
| `upper_corner` | 工作面上隅角：采空区漏出气体最先到达的位置，自燃早期的 CO 在此最先显现 |
| `return_airway` | 工作面回风巷：与上隅角交叉校核，判断气体是否随风流带出 |
| `intake_airway` | 工作面进风巷：进风侧参照，用于排除外来气体 |
| `goaf_gas_tube` | 采空区束管取样点：多组分气体分析（预警、阶段、爆炸三角形的主要依据） |
| `goaf_temperature` | 采空区测温点：采空区内部温度（辅助判断，也用于爆炸三角形的温度补偿） |
| `main_return` | 总回风巷：全矿回风参照 |
| `other` | 其他测点 |

## 工具详情

### 自描述

#### `get_capabilities`

接口版本，以及本矿实际提供哪些工具和可选功能。对接前先调用它；工具和功能以此为准。

无参数。

返回：`MineCapabilities`

#### `describe_mine`

本矿有哪些工作面（进回风巷、工作面、采空区、避难硐室分别是哪条巷道），以及测点用到了哪些语义角色。 技能按角色取数（list_sensors 里每个测点有 panel 和 role），不要写死测点编号。

无参数。

返回：`MineDescription`

### 时间

#### `get_clock`

返回矿井当前时间。研判时一律以此为“现在”。

无参数。

返回：`MineClock`

### 监测数据

#### `list_sensors`

返回全部测点：来源（安全监控、束管、测温）、可测参数、所在巷道和采空区深度。

无参数。

返回：`list[SensorPoint]`

#### `get_latest`

各测点的最新读数。sensor_ids 缺省为全部测点。

参数：

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `sensor_ids` | list[string] \| null | 否，缺省 None |  |

返回：`list[Reading]`

#### `get_readings`

查询测点历史读数。start/end 为 ISO 8601 时间，缺省为最近 24 小时。 安全监控 15 分钟一个点，束管 1 小时，测温 30 分钟。单次最多返回 5000 条。

参数：

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `sensor_ids` | list[string] | 是 |  |
| `start` | string \| null | 否，缺省 None |  |
| `end` | string \| null | 否，缺省 None |  |
| `measurands` | list[Measurand] \| null | 否，缺省 None |  |

返回：`list[Reading]`

#### `get_events`

最近若干小时安全监控系统自身产生的事件：测点超报警值、测点故障（无数据）。 这是矿井监控系统的记录，不包含 rescue-agent 按红线规则重算出的报警（fire-assessment 的 alarms）； 后者没有出现在这里是正常的，不代表报警未留痕。矿井报警平台的报警（含确认状态）用 get_alarms。

参数：

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `hours` | number | 否，缺省 24 |  |

返回：`list[MineEvent]`

#### `get_fire_areas`

火区管理台账：封闭过的火区、所在工作面、状态（sealed 已封闭 / unsealed 已启封）、封闭和启封时间。 从未封闭的发火区域不在台账里。

无参数。

返回：`list[FireArea]`

### 通风网络

#### `get_network`

返回通风网络：节点（含井口、避难硐室）和巷道（风向为 from_node→to_node，含长度、风阻、设计风量）。

无参数。

返回：`MineNetwork`

### 人员物资

#### `get_personnel`

井下人员实时位置（人员定位系统）。已升井人员不在名单中。 井下总人数、各班组人数、各巷道人数照抄 total、by_team、by_roadway，不要自己数名单。

无参数。

返回：`PersonnelSnapshot`

#### `get_inventory`

应急物资台账：类型、数量、存放位置（网络节点）。

无参数。

返回：`list[ResourceItem]`

#### `get_rescue_teams`

救援力量台账：救护队、人数、驻地、响应时间。

无参数。

返回：`list[RescueTeam]`

### 矿井自带报警

#### `get_alarms`

矿井报警平台的报警（拉取）：最近若干小时内越限的测点，含当前是否仍越限、是否已被值班人员确认。 这是矿井自己的报警，不是 fire-assessment 重算的结果；active_only 只返回仍越限的。

参数：

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `hours` | number | 否，缺省 24 |  |
| `active_only` | boolean | 否，缺省 False |  |

返回：`list[MineAlarm]`

### 设备

#### `get_equipment_status`

风机、水泵、制氮、注浆设备的运行状态。模拟矿井里制氮机组、注浆站在处置（注惰、灌浆）被执行后转为运行。

无参数。

返回：`list[EquipmentStatus]`

### 通知与指令

#### `send_notice`（写入）

发送预警、撤人等通知，返回回执。相同 idempotency_key 重复提交只发一次。

参数：

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `request` | NoticeRequest | 是 |  |

返回：`MineReceipt`

#### `dispatch_order`（写入）

下发物资、人员调度，返回回执。真实矿井异步生效：状态先为 executing，用 get_action_status 查询完成情况。

参数：

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `request` | DispatchRequest | 是 |  |

返回：`MineReceipt`

#### `submit_treatment`（写入）

提交处置动作（注惰、灌浆、封闭等），返回回执。模拟矿井里动作作用于物理模型；不支持的类型返回 rejected 回执。 只执行，不做红线校验。

参数：

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `request` | TreatmentRequest | 是 |  |

返回：`MineReceipt`

#### `get_action_status`

按回执编号查询通知、调度、处置的执行状态（异步生效的动作用它跟踪）。

参数：

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `action_id` | string | 是 |  |

返回：`MineReceipt`

## 数据结构

以上工具用到的结构（字段后“可省略”表示有缺省值）。完整 JSON Schema 在 `mcp/contract/schema/`（`make schema` 导出）。

### ActionKind

枚举值：`notice`、`dispatch`、`treatment`

### ActionStatus

枚举值：`accepted`、`executing`、`completed`、`failed`、`rejected`

### AirwayKind

枚举值：`intake`、`return`、`working_face`、`goaf`、`other`

### AlarmLevel

枚举值：`warning`、`alarm`、`critical`

### AlarmStatus

枚举值：`active`、`cleared`

### DispatchLine

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `item_type` | string | 是 |  |
| `quantity` | number | 是 |  |
| `unit` | string | 是 |  |
| `from_node` | string | 是 |  |
| `to_node` | string | 是 |  |
| `eta_min` | number \| null | 否，缺省 None |  |

### DispatchRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `purpose` | string | 是 |  |
| `lines` | list[DispatchLine] | 是 |  |
| `idempotency_key` | string | 是 |  |

### EquipmentKind

枚举值：`main_fan`、`local_fan`、`pump`、`nitrogen_plant`、`grout_station`、`other`

### EquipmentState

枚举值：`running`、`standby`、`stopped`、`fault`

### EquipmentStatus

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `equipment_id` | string | 是 |  |
| `name` | string | 是 |  |
| `kind` | EquipmentKind | 是 |  |
| `node_id` | string \| null | 否，缺省 None |  |
| `state` | EquipmentState | 是 |  |
| `updated_at` | MineTime | 是 | 矿井本地时间，ISO 8601，不带时区，如 2026-11-07T08:00:00 |

### EventKind

枚举值：`sensor_alarm`、`sensor_fault`、`manual_report`

### FireArea

火区管理台账中的一条：封闭过的火区及其状态。 来自矿井的火区台账（人工登记的事实），不是模拟器真值。从未封闭的发火区域不在台账里。 复燃征兆（第164条）等只对封闭火区或启封后的火区有意义的判据，依据这里的状态决定是否适用。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `area_id` | string | 是 |  |
| `panel` | string | 是 |  |
| `name` | string | 是 |  |
| `status` | FireAreaStatus | 是 |  |
| `sealed_at` | MineTime | 是 | 矿井本地时间，ISO 8601，不带时区，如 2026-11-07T08:00:00 |
| `unsealed_at` | MineTime \| null | 否，缺省 None |  |

### FireAreaStatus

枚举值：`sealed`、`unsealed`

### Measurand

枚举值：`CO`、`CO2`、`CH4`、`O2`、`N2`、`C2H4`、`C2H2`、`C2H6`、`H2`、`TEMP`、`WIND`

### MineAlarm

矿井自己的报警平台里的一条报警（拉取，含确认状态）。不是 fire-assessment 重算的结果。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `alarm_id` | string | 是 |  |
| `sensor_id` | string \| null | 否，缺省 None |  |
| `measurand` | Measurand \| null | 否，缺省 None |  |
| `level` | AlarmLevel | 是 |  |
| `status` | AlarmStatus | 是 |  |
| `raised_at` | MineTime | 是 | 矿井本地时间，ISO 8601，不带时区，如 2026-11-07T08:00:00 |
| `cleared_at` | MineTime \| null | 否，缺省 None |  |
| `value` | number \| null | 否，缺省 None |  |
| `threshold` | number \| null | 否，缺省 None |  |
| `acknowledged` | boolean | 否，缺省 False |  |
| `acknowledged_at` | MineTime \| null | 否，缺省 None |  |
| `message` | string | 是 |  |

### MineCapabilities

get_capabilities 的返回：接口版本，以及本矿实际提供哪些工具和功能。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `interface_version` | string | 是 |  |
| `contract_version` | string | 是 |  |
| `mine_id` | string | 是 |  |
| `implementation` | string | 是 |  |
| `tools` | list[string] | 是 |  |
| `features` | list[string] | 是 |  |

### MineClock

矿井当前时间。模拟矿井里它是模拟时钟；所有研判都以它为“现在”。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `now` | MineTime | 是 | 矿井本地时间，ISO 8601，不带时区，如 2026-11-07T08:00:00 |

### MineDescription

describe_mine 的返回：本矿有哪些工作面、测点用到了哪些角色。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `mine_id` | string | 是 |  |
| `name` | string | 是 |  |
| `panels` | list[Panel] | 是 |  |
| `sensor_roles` | list[SensorRole] | 是 |  |

### MineEvent

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `event_id` | string | 是 |  |
| `kind` | EventKind | 是 |  |
| `ts` | MineTime | 是 | 矿井本地时间，ISO 8601，不带时区，如 2026-11-07T08:00:00 |
| `sensor_id` | string \| null | 否，缺省 None |  |
| `roadway_id` | string \| null | 否，缺省 None |  |
| `message` | string | 是 |  |

### MineNetwork

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `mine_id` | string | 是 |  |
| `nodes` | list[Node] | 是 |  |
| `roadways` | list[Roadway] | 是 |  |

### MineReceipt

通知、指令的回执。真实矿井异步生效时，用 get_action_status 查后续状态。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `action_id` | string | 是 |  |
| `kind` | ActionKind | 是 |  |
| `status` | ActionStatus | 是 |  |
| `received_at` | MineTime | 是 | 矿井本地时间，ISO 8601，不带时区，如 2026-11-07T08:00:00 |
| `updated_at` | MineTime | 是 | 矿井本地时间，ISO 8601，不带时区，如 2026-11-07T08:00:00 |
| `idempotency_key` | string | 是 |  |
| `detail` | string \| null | 否，缺省 None |  |

### Node

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 |  |
| `name` | string | 是 |  |
| `is_surface` | boolean | 否，缺省 False |  |
| `is_refuge` | boolean | 否，缺省 False |  |

### NoticeKind

枚举值：`warning`、`evacuation`、`info`

### NoticeRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `kind` | NoticeKind | 是 |  |
| `message` | string | 是 |  |
| `panel` | string \| null | 否，缺省 None |  |
| `level` | string \| null | 否，缺省 None |  |
| `targets` | list[string] | 否，可省略 |  |
| `idempotency_key` | string | 是 |  |

### Panel

一个工作面的拓扑：它的进回风巷、工作面、采空区和避难硐室分别是网络里的哪条巷道/哪个节点。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `panel_id` | string | 是 |  |
| `name` | string | 是 |  |
| `kind` | PanelKind | 是 |  |
| `intake_roadway_id` | string \| null | 否，缺省 None |  |
| `return_roadway_id` | string \| null | 否，缺省 None |  |
| `face_roadway_id` | string \| null | 否，缺省 None |  |
| `goaf_roadway_id` | string \| null | 否，缺省 None |  |
| `refuge_node_id` | string \| null | 否，缺省 None |  |

### PanelKind

枚举值：`longwall`、`heading`

### PersonLocation

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `person_id` | string | 是 |  |
| `role` | string | 是 |  |
| `team` | string \| null | 否，缺省 None |  |
| `roadway_id` | string | 是 |  |
| `ts` | MineTime | 是 | 矿井本地时间，ISO 8601，不带时区，如 2026-11-07T08:00:00 |

### PersonnelSnapshot

get_personnel 的返回：人员名单和人数。人数由 of() 统一计算，Agent 照抄，不要自己数名单。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `total` | integer | 是 |  |
| `by_team` | list[TeamCount] | 是 |  |
| `by_roadway` | list[RoadwayCount] | 是 |  |
| `people` | list[PersonLocation] | 是 |  |

### Quality

枚举值：`ok`、`suspect`、`missing`

### Reading

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `sensor_id` | string | 是 |  |
| `measurand` | Measurand | 是 |  |
| `ts` | MineTime | 是 | 矿井本地时间，ISO 8601，不带时区，如 2026-11-07T08:00:00 |
| `value` | number \| null | 是 |  |
| `quality` | Quality | 否，缺省 'ok' |  |

### RescueTeam

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `team_id` | string | 是 |  |
| `name` | string | 是 |  |
| `members` | integer | 是 |  |
| `base_node` | string | 是 |  |
| `response_min` | number | 是 |  |

### ResourceItem

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `item_type` | string | 是 |  |
| `name` | string | 是 |  |
| `quantity` | number | 是 |  |
| `unit` | string | 是 |  |
| `depot_node` | string | 是 |  |

### Roadway

通风网络中的一条分支（巷道）。风向为 from_node → to_node。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 |  |
| `name` | string | 是 |  |
| `from_node` | string | 是 |  |
| `to_node` | string | 是 |  |
| `kind` | AirwayKind | 是 |  |
| `length_m` | number | 是 |  |
| `resistance_ns2_m8` | number \| null | 否，缺省 None |  |
| `design_airflow_m3_min` | number \| null | 否，缺省 None |  |

### RoadwayCount

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `roadway_id` | string | 是 |  |
| `count` | integer | 是 |  |

### SensorPoint

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 |  |
| `name` | string | 是 |  |
| `source` | SensorSource | 是 |  |
| `measurands` | list[Measurand] | 是 |  |
| `roadway_id` | string | 是 |  |
| `position_m` | number \| null | 否，缺省 None |  |
| `goaf_depth_m` | number \| null | 否，缺省 None |  |
| `panel` | string \| null | 否，缺省 None |  |
| `role` | SensorRole | 否，缺省 'other' |  |

### SensorRole

测点的语义角色：技能按角色取数，不按测点编号（换矿只要 describe_mine 如实回答，技能不改）。 角色按“位置/用途”划分，测什么由 measurands 说明。占位词表（TODO(专家)：演示后补全）。

枚举值：`upper_corner`、`return_airway`、`intake_airway`、`goaf_gas_tube`、`goaf_temperature`、`main_return`、`other`

### SensorSource

枚举值：`safety_monitor`、`bundle_tube`、`temperature`

### StepType

方案步骤类型。红线规则按步骤类型和顺序做确定性校验，所以方案必须结构化。

枚举值：`monitor`、`evacuate`、`power_cutoff`、`inert_injection`、`grouting`、`inhibitor_spray`、`leak_sealing`、`pressure_balancing`、`temporary_seal`、`permanent_seal`、`reverse_ventilation`、`short_circuit_ventilation`、`stop_main_fan`、`reduce_airflow`、`enter_disaster_area`、`direct_firefighting`、`water_firefighting`、`inspect_sealed_area`、`unseal`

### TeamCount

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `team` | string \| null | 是 |  |
| `count` | integer | 是 |  |

### TreatmentRequest

处置动作：注惰、灌浆、封闭等。矿井侧只执行，不做红线校验（校验在 rescue-agent 的 guard）。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `panel` | string \| null | 否，缺省 None |  |
| `type` | StepType | 是 |  |
| `roadways` | list[string] | 否，可省略 |  |
| `params` | map[string, number \| integer \| string \| boolean] | 否，可省略 |  |
| `idempotency_key` | string | 是 |  |

## 对接新矿井

1. 实现“必需”工具，再按矿井实际能力实现可选功能；`get_capabilities` 如实列出 `tools` 和 `features`。
2. `describe_mine` 如实回答工作面拓扑；`list_sensors` 给每个测点标 `panel` 和 `role`。
3. 写入类工具只执行、不判断；返回回执，异步生效的用 `get_action_status` 查询；相同 `idempotency_key` 重复提交只执行一次。
4. 运行一致性检查：`python -m rescue_contract.conformance <URL>`。写入类检查会真的执行动作，**只对模拟矿井或测试环境加 `--with-actions`**。
5. 一致性检查通过后，把 `RESCUE_MCP_MINE_DATA_URL` 指向适配器；rescue-agent 一侧不用改。
