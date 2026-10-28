# 0005 模拟矿井与真实矿井共用同一契约

状态：采纳

## 背景

前期没有可用的真实矿井作为开发和试运行场地，决定先建模拟矿井，后期替换。替换是否顺利取决于边界划在哪里。

## 决定

- 边界是 `mine_data` MCP 服务的工具集合（get_network、list_sensors、get_readings、get_latest、get_events、get_personnel、get_inventory、get_rescue_teams、get_clock）和 `rescue_contract.models` 的数据模型。
- 其他服务通过 `rescue_contract.client.MineData` 读数据，不直接依赖模拟器。
- 模拟器的控制接口（`/sim/*`）和真实状态（truth）不属于契约，智能体看不到。
- `rescue_contract.conformance` 是适配器的验收测试：模拟器和真实适配器都必须通过。
- 矿井时间（`get_clock`）由数据源给出；所有时效判断用矿井时间，便于模拟加速。

## 后果

- 真实矿井接入的工作量集中在一个适配器包；智能体、技能、算法不改。
- 契约变更影响面大，需要评审并重新导出 JSON Schema（CI 检查）。
- 模拟器参数不代表真实煤层，评测结论只说明“逻辑正确”，不说明“阈值正确”。
