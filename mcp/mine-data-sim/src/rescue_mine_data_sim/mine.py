"""示范矿井：一个综采工作面（1201）加一个掘进工作面（1202）的简化矿井。

这是一期演示用的“典型矿井”占位版本。阶段 1 用设计院或高校提供的脱敏图纸替换：
巷道长度、风阻、设计风量、测点布置都应来自真实图纸，否则通风解算和避灾路线不可信。
TODO(数据): 用典型矿井脱敏图纸替换本文件中的网络和测点。
"""

from __future__ import annotations

from rescue_contract.models import (
    AirwayKind,
    Measurand,
    MineNetwork,
    Node,
    RescueTeam,
    ResourceItem,
    Roadway,
    SensorPoint,
    SensorSource,
)

MINE_ID = "demo-mine"

NODES = [
    Node(id="N0", name="主井口", is_surface=True),
    Node(id="N1", name="主井底车场"),
    Node(id="N2", name="1201运输巷口"),
    Node(id="N3", name="1201工作面下端头"),
    Node(id="N4", name="1201工作面上端头（上隅角）"),
    Node(id="N5", name="1201回风巷口"),
    Node(id="N6", name="回风井底"),
    Node(id="N7", name="回风井口", is_surface=True),
    Node(id="N8", name="1201避难硐室", is_refuge=True),
    Node(id="N9", name="1202掘进工作面"),
]

R = AirwayKind
ROADWAYS = [
    Roadway(
        id="R-MAIN-SHAFT",
        name="主井",
        from_node="N0",
        to_node="N1",
        kind=R.INTAKE,
        length_m=600,
        resistance_ns2_m8=0.012,
        design_airflow_m3_min=9000,
    ),
    Roadway(
        id="R-MAIN-IN",
        name="主运输大巷",
        from_node="N1",
        to_node="N2",
        kind=R.INTAKE,
        length_m=1500,
        resistance_ns2_m8=0.035,
        design_airflow_m3_min=6500,
    ),
    Roadway(
        id="R-1201-IN",
        name="1201运输巷",
        from_node="N2",
        to_node="N3",
        kind=R.INTAKE,
        length_m=1800,
        resistance_ns2_m8=0.060,
        design_airflow_m3_min=1800,
    ),
    Roadway(
        id="R-1201-FACE",
        name="1201工作面",
        from_node="N3",
        to_node="N4",
        kind=R.WORKING_FACE,
        length_m=220,
        resistance_ns2_m8=0.080,
        design_airflow_m3_min=1700,
    ),
    Roadway(
        id="R-1201-GOAF",
        name="1201采空区漏风通道",
        from_node="N3",
        to_node="N4",
        kind=R.GOAF,
        length_m=250,
        resistance_ns2_m8=8.0,
        design_airflow_m3_min=100,
    ),
    Roadway(
        id="R-1201-RET",
        name="1201回风巷",
        from_node="N4",
        to_node="N5",
        kind=R.RETURN,
        length_m=1800,
        resistance_ns2_m8=0.065,
        design_airflow_m3_min=1800,
    ),
    Roadway(
        id="R-RET-MAIN",
        name="总回风巷",
        from_node="N5",
        to_node="N6",
        kind=R.RETURN,
        length_m=1500,
        resistance_ns2_m8=0.040,
        design_airflow_m3_min=6500,
    ),
    Roadway(
        id="R-RET-SHAFT",
        name="回风井",
        from_node="N6",
        to_node="N7",
        kind=R.RETURN,
        length_m=600,
        resistance_ns2_m8=0.015,
        design_airflow_m3_min=9000,
    ),
    Roadway(id="R-REFUGE", name="避难硐室联络巷", from_node="N2", to_node="N8", kind=R.OTHER, length_m=50),
    Roadway(
        id="R-1202-IN",
        name="1202掘进巷进风",
        from_node="N1",
        to_node="N9",
        kind=R.INTAKE,
        length_m=900,
        resistance_ns2_m8=0.050,
        design_airflow_m3_min=2500,
    ),
    Roadway(
        id="R-1202-RET",
        name="1202回风联络巷",
        from_node="N9",
        to_node="N6",
        kind=R.RETURN,
        length_m=1000,
        resistance_ns2_m8=0.050,
        design_airflow_m3_min=2500,
    ),
]

NETWORK = MineNetwork(mine_id=MINE_ID, nodes=NODES, roadways=ROADWAYS)

M = Measurand
TUBE = [M.O2, M.N2, M.CO, M.CO2, M.CH4, M.C2H4, M.C2H2]
SENSORS = [
    SensorPoint(
        id="SM-1201-UC",
        name="1201上隅角",
        source=SensorSource.SAFETY_MONITOR,
        measurands=[M.CO, M.CH4, M.O2, M.TEMPERATURE],
        roadway_id="R-1201-FACE",
        position_m=215,
    ),
    SensorPoint(
        id="SM-1201-RET",
        name="1201回风巷",
        source=SensorSource.SAFETY_MONITOR,
        measurands=[M.CO, M.CH4, M.TEMPERATURE, M.WIND_SPEED],
        roadway_id="R-1201-RET",
        position_m=30,
    ),
    SensorPoint(
        id="SM-1201-IN",
        name="1201运输巷",
        source=SensorSource.SAFETY_MONITOR,
        measurands=[M.CO, M.CH4, M.WIND_SPEED],
        roadway_id="R-1201-IN",
        position_m=1700,
    ),
    SensorPoint(
        id="SM-RET-MAIN",
        name="总回风巷",
        source=SensorSource.SAFETY_MONITOR,
        measurands=[M.CO, M.CH4, M.WIND_SPEED],
        roadway_id="R-RET-MAIN",
        position_m=100,
    ),
    SensorPoint(
        id="BT-1201-G1",
        name="1201采空区束管1号",
        source=SensorSource.BUNDLE_TUBE,
        measurands=TUBE,
        roadway_id="R-1201-GOAF",
        goaf_depth_m=20,
    ),
    SensorPoint(
        id="BT-1201-G2",
        name="1201采空区束管2号",
        source=SensorSource.BUNDLE_TUBE,
        measurands=TUBE,
        roadway_id="R-1201-GOAF",
        goaf_depth_m=50,
    ),
    SensorPoint(
        id="BT-1201-G3",
        name="1201采空区束管3号",
        source=SensorSource.BUNDLE_TUBE,
        measurands=TUBE,
        roadway_id="R-1201-GOAF",
        goaf_depth_m=80,
    ),
    SensorPoint(
        id="TP-1201-G1",
        name="1201采空区测温1号",
        source=SensorSource.TEMPERATURE,
        measurands=[M.TEMPERATURE],
        roadway_id="R-1201-GOAF",
        goaf_depth_m=20,
    ),
    SensorPoint(
        id="TP-1201-G2",
        name="1201采空区测温2号",
        source=SensorSource.TEMPERATURE,
        measurands=[M.TEMPERATURE],
        roadway_id="R-1201-GOAF",
        goaf_depth_m=50,
    ),
    SensorPoint(
        id="TP-1201-G3",
        name="1201采空区测温3号",
        source=SensorSource.TEMPERATURE,
        measurands=[M.TEMPERATURE],
        roadway_id="R-1201-GOAF",
        goaf_depth_m=80,
    ),
]
SENSOR_BY_ID = {s.id: s for s in SENSORS}

INVENTORY = [
    ResourceItem(item_type="nitrogen_capacity_m3_h", name="地面制氮机组（产能）", quantity=1200, unit="m³/h", depot_node="N0"),
    ResourceItem(item_type="grout_material_t", name="注浆材料（黄泥/粉煤灰）", quantity=300, unit="t", depot_node="N1"),
    ResourceItem(item_type="inhibitor_t", name="阻化剂", quantity=8, unit="t", depot_node="N2"),
    ResourceItem(item_type="temporary_seal_kit", name="快速密闭材料", quantity=6, unit="套", depot_node="N2"),
    ResourceItem(item_type="self_rescuer", name="隔绝式自救器", quantity=120, unit="台", depot_node="N1"),
    ResourceItem(item_type="fire_extinguisher", name="干粉灭火器", quantity=40, unit="具", depot_node="N2"),
]

RESCUE_TEAMS = [
    RescueTeam(team_id="RT-PRO-1", name="矿山救护大队一中队", members=30, base_node="N0", response_min=25),
    RescueTeam(team_id="RT-PART-1", name="矿兼职救护队", members=18, base_node="N1", response_min=10),
]
