"""数据契约 v0.1：模拟矿井与真实矿井适配器共同遵守的数据模型。

所有 MCP 服务器之间、以及服务器与智能体之间交换的数据都使用这里的模型。
修改字段前先改版本号（CONTRACT_VERSION），并同步更新 schema/ 下导出的 JSON Schema
（运行 `python -m rescue_contract.export_schema`）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, WithJsonSchema

CONTRACT_VERSION = "0.6.0"


def _no_tz(v: datetime) -> datetime:
    if v.tzinfo is not None:
        raise ValueError("矿井时间是不带时区的矿井本地时间，请去掉时区偏移（如 +08:00、Z）")
    return v


# 矿井本地时间，不带时区（矿井监控系统的惯例）。不用 pydantic 默认的 `format: date-time`：
# 它按 RFC 3339 要求带时区偏移，DSH 的 MCP 客户端严格校验输出时会拒绝不带时区的值。
MineTime = Annotated[
    datetime,
    AfterValidator(_no_tz),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$",
            "description": "矿井本地时间，ISO 8601，不带时区，如 2026-11-07T08:00:00",
        }
    ),
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ── 矿井结构 ────────────────────────────────────────────────────────────────


class AirwayKind(StrEnum):
    INTAKE = "intake"  # 进风
    RETURN = "return"  # 回风
    WORKING_FACE = "working_face"  # 工作面
    GOAF = "goaf"  # 采空区（漏风通道）
    OTHER = "other"


class Node(_Model):
    id: str
    name: str
    is_surface: bool = False  # 地面出口（井口）
    is_refuge: bool = False  # 避难硐室


class Roadway(_Model):
    """通风网络中的一条分支（巷道）。风向为 from_node → to_node。"""

    id: str
    name: str
    from_node: str
    to_node: str
    kind: AirwayKind
    length_m: float = Field(gt=0)
    resistance_ns2_m8: float | None = None  # 风阻，N·s²/m⁸
    design_airflow_m3_min: float | None = None


class MineNetwork(_Model):
    mine_id: str
    nodes: list[Node]
    roadways: list[Roadway]


class MineClock(_Model):
    """矿井当前时间。模拟矿井里它是模拟时钟；所有研判都以它为“现在”。"""

    now: MineTime


# ── 测点与监测数据 ──────────────────────────────────────────────────────────


class Measurand(StrEnum):
    CO = "CO"  # ppm
    CO2 = "CO2"  # %
    CH4 = "CH4"  # %
    O2 = "O2"  # %
    N2 = "N2"  # %
    C2H4 = "C2H4"  # ppm
    C2H2 = "C2H2"  # ppm
    C2H6 = "C2H6"  # ppm
    H2 = "H2"  # ppm（束管色谱）
    TEMPERATURE = "TEMP"  # °C
    WIND_SPEED = "WIND"  # m/s


UNITS: dict[Measurand, str] = {
    Measurand.CO: "ppm",
    Measurand.CO2: "%",
    Measurand.CH4: "%",
    Measurand.O2: "%",
    Measurand.N2: "%",
    Measurand.C2H4: "ppm",
    Measurand.C2H2: "ppm",
    Measurand.C2H6: "ppm",
    Measurand.H2: "ppm",
    Measurand.TEMPERATURE: "°C",
    Measurand.WIND_SPEED: "m/s",
}


class SensorSource(StrEnum):
    SAFETY_MONITOR = "safety_monitor"  # 安全监控系统
    BUNDLE_TUBE = "bundle_tube"  # 束管监测
    TEMPERATURE = "temperature"  # 测温系统（测点或光纤）


class SensorPoint(_Model):
    id: str
    name: str
    source: SensorSource
    measurands: list[Measurand]
    roadway_id: str
    position_m: float | None = None  # 距巷道起点距离
    goaf_depth_m: float | None = None  # 束管/测温点距工作面的采空区深度


class Quality(StrEnum):
    OK = "ok"
    SUSPECT = "suspect"  # 疑似异常（漂移、卡值等）
    MISSING = "missing"  # 缺数


class Reading(_Model):
    sensor_id: str
    measurand: Measurand
    ts: MineTime
    value: float | None  # quality == missing 时为 None
    quality: Quality = Quality.OK


# ── 人员与物资 ──────────────────────────────────────────────────────────────


class PersonLocation(_Model):
    person_id: str
    role: str  # 采煤工、瓦斯检查员、带班领导……
    team: str | None = None
    roadway_id: str
    ts: MineTime


class TeamCount(_Model):
    team: str | None
    count: int


class RoadwayCount(_Model):
    roadway_id: str
    count: int


class PersonnelSnapshot(_Model):
    """get_personnel 的返回：人员名单和人数。人数由 of() 统一计算，Agent 照抄，不要自己数名单。"""

    total: int
    by_team: list[TeamCount]
    by_roadway: list[RoadwayCount]
    people: list[PersonLocation]

    @classmethod
    def of(cls, people: list[PersonLocation]) -> PersonnelSnapshot:
        teams: dict[str | None, int] = {}
        roadways: dict[str, int] = {}
        for p in people:
            teams[p.team] = teams.get(p.team, 0) + 1
            roadways[p.roadway_id] = roadways.get(p.roadway_id, 0) + 1
        return cls(
            total=len(people),
            by_team=[TeamCount(team=t, count=n) for t, n in sorted(teams.items(), key=lambda kv: kv[0] or "")],
            by_roadway=[RoadwayCount(roadway_id=r, count=n) for r, n in sorted(roadways.items())],
            people=people,
        )


class ResourceItem(_Model):
    item_type: str  # nitrogen_m3、grout_m3、inhibitor_t、self_rescuer、temporary_seal_kit……
    name: str
    quantity: float
    unit: str
    depot_node: str  # 存放位置（网络节点）


class RescueTeam(_Model):
    team_id: str
    name: str
    members: int
    base_node: str
    response_min: float  # 到达矿井的响应时间


# ── 事件 ───────────────────────────────────────────────────────────────────


class EventKind(StrEnum):
    SENSOR_ALARM = "sensor_alarm"
    SENSOR_FAULT = "sensor_fault"
    MANUAL_REPORT = "manual_report"  # 人工上报（现场侦察等）


class MineEvent(_Model):
    event_id: str
    kind: EventKind
    ts: MineTime
    sensor_id: str | None = None
    roadway_id: str | None = None
    message: str


# ── 火区台账 ───────────────────────────────────────────────────────────────


class FireAreaStatus(StrEnum):
    SEALED = "sealed"  # 已封闭（临时或永久密闭）
    UNSEALED = "unsealed"  # 已启封


class FireArea(_Model):
    """火区管理台账中的一条：封闭过的火区及其状态。

    来自矿井的火区台账（人工登记的事实），不是模拟器真值。从未封闭的发火区域不在台账里。
    复燃征兆（第164条）等只对封闭火区或启封后的火区有意义的判据，依据这里的状态决定是否适用。
    """

    area_id: str
    panel: str  # 所在工作面编号，如 "1201"
    name: str
    status: FireAreaStatus
    sealed_at: MineTime
    unsealed_at: MineTime | None = None


# ── 处置方案（需人工确认的结构化指令）────────────────────────────────────────


class StepType(StrEnum):
    """方案步骤类型。红线规则按步骤类型和顺序做确定性校验，所以方案必须结构化。"""

    MONITOR = "monitor"  # 检测、观测（含启封前检查）
    EVACUATE = "evacuate"  # 撤人
    POWER_CUTOFF = "power_cutoff"  # 切断电源
    INERT_INJECTION = "inert_injection"  # 注氮、注二氧化碳等惰性气体
    GROUTING = "grouting"  # 注浆
    INHIBITOR_SPRAY = "inhibitor_spray"  # 喷洒阻化剂
    LEAK_SEALING = "leak_sealing"  # 堵漏
    PRESSURE_BALANCING = "pressure_balancing"  # 均压
    TEMPORARY_SEAL = "temporary_seal"  # 临时密闭
    PERMANENT_SEAL = "permanent_seal"  # 永久密闭或防爆密闭
    REVERSE_VENTILATION = "reverse_ventilation"  # 反风（局部或全矿井）
    SHORT_CIRCUIT_VENTILATION = "short_circuit_ventilation"  # 风流短路
    STOP_MAIN_FAN = "stop_main_fan"  # 停主要通风机
    REDUCE_AIRFLOW = "reduce_airflow"  # 减风
    ENTER_DISASTER_AREA = "enter_disaster_area"  # 进入灾区（探察、救人、作业）
    DIRECT_FIREFIGHTING = "direct_firefighting"  # 直接灭火（非水）
    WATER_FIREFIGHTING = "water_firefighting"  # 用水灭火
    INSPECT_SEALED_AREA = "inspect_sealed_area"  # 进入检查或加固密闭
    UNSEAL = "unseal"  # 启封


class PlanStep(_Model):
    order: int = Field(ge=1)
    type: StepType
    description: str
    roadways: list[str] = Field(default_factory=list)
    team_size: int | None = None  # 进入灾区的小队人数
    standby_team: bool | None = None  # 是否设待机小队
    params: dict[str, float | int | str | bool] = Field(default_factory=dict)


class ExplosionRisk(StrEnum):
    EXPLOSIVE = "explosive"  # 处于爆炸范围
    MAY_BECOME_EXPLOSIVE = "may_become_explosive"  # 混入空气后可能爆炸
    NOT_EXPLOSIVE = "not_explosive"
    UNKNOWN = "unknown"


class FireType(StrEnum):
    GOAF_SPONTANEOUS = "goaf_spontaneous_combustion"
    BELT_ROADWAY = "belt_roadway"
    OTHER = "other"


class FireContext(_Model):
    """校验方案时依据的灾区现场条件。未知就填 None，规则对未知值按“不放行”处理。"""

    fire_type: FireType
    as_of: MineTime
    methane_pct: float | None = None
    methane_rising: bool | None = None
    o2_pct: float | None = None
    area_temperature_c: float | None = None
    visibility_m: float | None = None
    explosion_risk: ExplosionRisk = ExplosionRisk.UNKNOWN
    fire_source_confirmed: bool | None = None
    water_supply_sufficient: bool | None = None
    return_airway_clear: bool | None = None
    sealed_at: MineTime | None = None


class Citation(_Model):
    document: str
    article: str | None = None  # 条号
    source: Literal["ragflow", "local-fallback", "data", "calculation"] = "ragflow"
    note: str | None = None


class PlanKind(StrEnum):
    FIREFIGHTING = "firefighting"  # 防灭火方案
    SEALING = "sealing"  # 封闭或灭火方案
    UNSEALING = "unsealing"  # 启封


class Plan(_Model):
    plan_id: str
    kind: PlanKind
    title: str
    context: FireContext
    steps: list[PlanStep] = Field(min_length=1)
    basis: list[Citation] = Field(default_factory=list)
    # 方案草案快照（workflow 的 draft_plan 生成）。发布时必须有：context 和必备步骤按它核对
    snapshot_id: str | None = None
    panel: str | None = None  # 工作面编号，用于在指令台账里按工作面查找


class HumanInput(_Model):
    """系统里没有数据、只能由人确认的现场条件（写明确认人）。"""

    field: Literal["area_temperature_c", "visibility_m", "fire_source_confirmed", "water_supply_sufficient", "return_airway_clear"]
    value: float | bool
    confirmed_by: str = Field(min_length=1)  # 确认人（姓名或岗位）
    note: str | None = None


# 人工确认项的中文名：方案草案的待补充清单、人工确认弹窗的说明共用
HUMAN_INPUT_LABELS: dict[str, str] = {
    "area_temperature_c": "灾区温度（℃）",
    "visibility_m": "能见度（米）",
    "fire_source_confirmed": "火源是否明确",
    "water_supply_sufficient": "水源、人力和物力是否充足",
    "return_airway_clear": "回风道是否畅通",
}


# ── 其他需确认的指令 ────────────────────────────────────────────────────────


class WarningLevel(StrEnum):
    NONE = "none"
    BLUE = "blue"  # 关注
    YELLOW = "yellow"  # 预警
    ORANGE = "orange"  # 严重
    RED = "red"  # 危险，建议升级为应急响应


class WarningNotice(_Model):
    location: str
    level: WarningLevel
    summary: str
    basis: list[Citation] = Field(default_factory=list)
    snapshot_id: str | None = None  # 研判快照；发布时必须有，等级按它核对
    override_reason: str | None = None  # 等级与研判快照不同时必须写明理由（人工改等级）


class DispatchLine(_Model):
    item_type: str
    quantity: float = Field(gt=0)
    unit: str
    from_node: str
    to_node: str
    eta_min: float | None = None


class DispatchOrder(_Model):
    order_id: str
    purpose: str
    lines: list[DispatchLine] = Field(min_length=1)
    snapshot_id: str | None = None  # 调度草案快照（workflow 的 draft_dispatch_for_plan）；有则逐行核对


class EvacuationNotice(_Model):
    reason: str
    persons: list[str]  # person_id
    routes: dict[str, list[str]] = Field(default_factory=dict)  # person_id → 节点路径
    supplementary: bool = True  # 智能体给的是“补充名单”，不替代预案撤人


class Escalation(_Model):
    reason: str
    recommended_level: str
    basis: list[Citation] = Field(default_factory=list)
    snapshot_id: str | None = None  # 升级建议快照（workflow 的 recommend_escalation）；发布时必须有


# ── 研判快照（workflow 服务生成，指令发布时按编号核对；见 docs/workflow-design.md）──


class SnapshotKind(StrEnum):
    ASSESSMENT = "assessment"  # 研判快照（预警等级、阶段、趋势、爆炸危险性、受威胁人员）
    EFFECT = "effect"  # 处置效果评估
    ESCALATION = "escalation"  # 升级为应急响应的建议
    PLAN_CONTEXT = "plan-context"  # 方案草案（现场条件和必备步骤）
    DISPATCH = "dispatch"  # 调度单草案


class Snapshot(_Model):
    """工作流的一次计算结果。只新增、不修改；方案、预警、升级都引用它的编号。"""

    snapshot_id: str
    kind: SnapshotKind
    panel: str
    as_of: MineTime  # 取数时的矿井时间
    created_at: MineTime  # 系统时间（本地），仅用于审计
    inputs: dict[str, Any] = Field(default_factory=dict)  # 调用参数，含人工输入及其确认人
    result: dict[str, Any]  # 工作流的结构化结果
    report_markdown: str | None = None  # 由模板生成的报告事实部分
    parent_ids: list[str] = Field(default_factory=list)  # 依赖的上游快照
