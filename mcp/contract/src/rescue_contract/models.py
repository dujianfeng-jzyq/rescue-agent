"""数据契约 v0.1：模拟矿井与真实矿井适配器共同遵守的数据模型。

所有 MCP 服务器之间、以及服务器与智能体之间交换的数据都使用这里的模型。
修改字段前先改版本号（CONTRACT_VERSION），并同步更新 schema/ 下导出的 JSON Schema
（运行 `python -m rescue_contract.export_schema`）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, WithJsonSchema, model_validator

CONTRACT_VERSION = "0.12.0"


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


class SensorRole(StrEnum):
    """测点的语义角色：技能按角色取数，不按测点编号（换矿只要 describe_mine 如实回答，技能不改）。

    角色按“位置/用途”划分，测什么由 measurands 说明。占位词表（TODO(专家)：演示后补全）。"""

    UPPER_CORNER = "upper_corner"  # 工作面上隅角
    RETURN_AIRWAY = "return_airway"  # 工作面回风巷
    INTAKE_AIRWAY = "intake_airway"  # 工作面进风巷
    GOAF_GAS_TUBE = "goaf_gas_tube"  # 采空区束管取样点
    GOAF_TEMPERATURE = "goaf_temperature"  # 采空区测温点
    MAIN_RETURN = "main_return"  # 总回风巷
    OTHER = "other"  # 其他（词表里没有的）


SENSOR_ROLE_INFO: dict[SensorRole, str] = {
    SensorRole.UPPER_CORNER: "工作面上隅角：采空区漏出气体最先到达的位置，自燃早期的 CO 在此最先显现",
    SensorRole.RETURN_AIRWAY: "工作面回风巷：与上隅角交叉校核，判断气体是否随风流带出",
    SensorRole.INTAKE_AIRWAY: "工作面进风巷：进风侧参照，用于排除外来气体",
    SensorRole.GOAF_GAS_TUBE: "采空区束管取样点：多组分气体分析（预警、阶段、爆炸三角形的主要依据）",
    SensorRole.GOAF_TEMPERATURE: "采空区测温点：采空区内部温度（辅助判断，也用于爆炸三角形的温度补偿）",
    SensorRole.MAIN_RETURN: "总回风巷：全矿回风参照",
    SensorRole.OTHER: "其他测点",
}


class SensorPoint(_Model):
    id: str
    name: str
    source: SensorSource
    measurands: list[Measurand]
    roadway_id: str
    position_m: float | None = None  # 距巷道起点距离
    goaf_depth_m: float | None = None  # 束管/测温点距工作面的采空区深度
    panel: str | None = None  # 所属工作面编号（全矿性测点为 None）
    role: SensorRole = SensorRole.OTHER  # 语义角色，技能按它取数


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
    source: Literal["ragflow", "local-fallback", "data", "calculation", "index"] = "ragflow"  # index：知识库插件的条文索引
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
    panel: str | None = None  # 工作面编号，用于在指令台账里按工作面查找
    # 方案来自哪条审核记录（plan-compliance/review，draft_sealing_plan 生成）。下发时只提交记录编号，
    # guard 从记录取方案，台账里的方案带上它
    record_id: str | None = None


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
    # 不再使用（保留字段以兼容旧调用）：发到矿井的预警正文由 guard 按记录生成（validation.warning_message），
    # 不用智能体写的文字，确认弹窗显示的就是发出去的正文（2026-11-11 代码复查）
    summary: str = ""
    basis: list[Citation] = Field(default_factory=list)
    # 预警等级的结果记录（fire-assessment/warning，run_fire_assessment 返回的 record_ids.warning），发布时按它核对等级
    record_id: str = Field(min_length=1)
    override_reason: str | None = None  # 等级高于计算结果时必须写明理由（人工往高调）；不能低于计算结果（guard 拒绝）


class DispatchLine(_Model):
    item_type: str
    quantity: float = Field(gt=0)
    unit: str
    from_node: str
    to_node: str
    eta_min: float | None = None


class DispatchOrder(_Model):
    # 给方案的审核记录（record_id，draft_sealing_plan 的 issue_record_id）而明细留空时，由 guard 按方案记录的“需调运”填写
    # 编号、用途和明细（dispatch_from_record）：明细不用智能体抄（2026-11-10 演示物资调运单）
    order_id: str = ""
    purpose: str = ""
    lines: list[DispatchLine] = Field(default_factory=list)
    record_id: str | None = None


class EvacuationNotice(_Model):
    # 通知正文。给了 record_id 且名单留空时由 guard 按记录生成（evacuation_from_record），智能体写的不用：
    # 2026-11-11 演示里智能体写的说明有错字（“预案撕人”）原样发到了矿井。只有指挥人员另外点名时才用这里的文字。
    reason: str = ""
    # person_id。留空并给 record_id 时，由 guard 按人员记录的受威胁人员和可达避灾路线填写（evacuation_from_record）：
    # 名单不用智能体抄（2026-11-10 演示复看：智能体把名单写成一句话被拦）
    persons: list[str] = Field(default_factory=list)
    routes: dict[str, list[str]] = Field(default_factory=dict)  # person_id → 节点路径
    supplementary: bool = True  # 智能体给的是“补充名单”，不替代预案撤人
    # 依据的人员记录（run_fire_assessment 返回的 record_ids.personnel）。只用来把这次撤人挂到该工作面的事件时间线和确认弹窗上；
    # 不核对时效、读不到也不拦：撤人不等待本系统。
    record_id: str | None = None


class Escalation(_Model):
    # 不再使用（保留字段以兼容旧调用）：升级正文由 guard 按升级建议记录生成（validation.escalation_message），
    # 升级理由和级别不由模型填写（CLAUDE.md 约定 2；2026-11-11 代码复查）
    reason: str = ""
    recommended_level: str = ""
    basis: list[Citation] = Field(default_factory=list)
    # 升级建议的结果记录（emergency-escalation/escalation，run_fire_assessment 返回的 record_ids.escalation），
    # 发布时按它核对结论
    record_id: str = Field(min_length=1)


# ── 第二版：记录与接口扩展点（docs/design-v2.md 4.3、4.4、4.11；ADR 0007）────────
# 记录由证据插件（DSH 进程，TypeScript）写入，actions 等 Python 侧只读并重算 content_sha256 核对。
# 第三版 P2 起第一版的研判快照（Snapshot、snapshot_id）已删除：指令只引用 record_id。


class Origin(_Model):
    """谁触发的。记录、台账、审计都带上，以后按渠道、调用方查询和限权。"""

    channel: Literal["web", "api", "subagent", "eval"]
    trigger: Literal["chat", "watch", "button", "flow"]  # 提问、持续检测、界面按钮（以后）、固定流程内部
    caller: str | None = None  # 用户、后台任务编号、appId
    conversation_id: str | None = None  # 本系统的会话编号（不是 DSH 的内部编号）


class RecordKind(StrEnum):
    EVIDENCE = "evidence"  # 按采集清单取到的数据
    RESULT = "result"  # 技能脚本的计算结果
    REPORT = "report"  # 按模板生成的报告


RECORD_PREFIX: dict[RecordKind, str] = {RecordKind.EVIDENCE: "EV", RecordKind.RESULT: "RS", RecordKind.REPORT: "RP"}

# <前缀>-<矿井时间 yyyymmddThhmmss>-<6 位小写字母或数字>，按时间可排序、不可猜测
RECORD_ID_PATTERN = r"^(EV|RS|RP)-\d{8}T\d{6}-[a-z0-9]{6}$"

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RecordFile(_Model):
    """记录附带的文件（风险图、报告文件等）。按记录编号和文件名下载，不按路径。"""

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    media_type: str
    size: int = Field(ge=0)
    sha256: Sha256


class Record(_Model):
    """证据、结果或报告。只新增、不修改；报告和指令按编号引用，发布前重算 content_sha256。

    content_sha256 = sha256(canonical_json(content))，规范化方式见 rescue_contract.canonical（RFC 8785）；
    content 为字符串时是其 UTF-8 字节的 sha256。只覆盖 content，不覆盖 source、kind、panel、as_of：
    防篡改靠记录目录的写权限（只有证据插件能写），见 rescue_contract.records。
    """

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    kind: RecordKind
    panel: str | None = None  # 全矿性的检测可以没有工作面
    as_of: MineTime  # 取数时的矿井时间
    created_at: AwareDatetime  # 系统时间（带时区），仅用于审计
    origin: Origin
    # 证据：采集清单和参数；结果：skill、skill_version、script、script_sha256、inputs、input_sha256；
    # 报告：skill、template、template_sha256
    source: dict[str, Any]
    content: dict[str, Any] | str
    content_sha256: Sha256
    files: list[RecordFile] = Field(default_factory=list)
    parent_ids: list[str] = Field(default_factory=list)  # 依赖的上游记录

    @model_validator(mode="after")
    def _prefix_matches_kind(self) -> Record:
        if not self.record_id.startswith(RECORD_PREFIX[self.kind] + "-"):
            raise ValueError(f"记录编号 {self.record_id} 的前缀与类型 {self.kind} 不符（应为 {RECORD_PREFIX[self.kind]}-）")
        return self


class NextAction(_Model):
    """结果卡片上的可选下一步。tool 为空时只是文字建议。"""

    label: str
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class ResultCard(_Model):
    """固定流程和风险检测工具返回的结构化结果；网页端和以后的对外接口各自呈现。"""

    title: str
    summary: str  # 结论摘要，数值照抄记录
    record_ids: list[str] = Field(default_factory=list)
    files: list[RecordFile] = Field(default_factory=list)  # 交付的文件（所属记录见 record_ids）
    next_actions: list[NextAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)  # 数据缺失、阶段未生成等需要提醒的事


class HumanInputItem(_Model):
    field: str  # 方案现场条件的字段名（见 sealing-plan 技能的人工确认项）
    label: str
    description: str | None = None
    value_type: Literal["bool", "number", "text", "choice"]
    options: list[str] = Field(default_factory=list)  # value_type 为 choice 时的可选值
    allow_text: bool = False  # 除选项外是否允许填写文字
    blocking: bool = True  # 缺了它是否影响红线、必须先补


class HumanInputRequest(_Model):
    """固定流程缺人工输入时返回的结构化问题。主预设转成 ask_user；无人值守的入口原样转给调用方。"""

    flow: str  # 哪个固定流程，如 draft_sealing_plan
    reason: str
    items: list[HumanInputItem] = Field(min_length=1)
    record_ids: list[str] = Field(default_factory=list)  # 已经生成的记录，重新运行时可复用


class ApprovalCheck(_Model):
    name: str  # 如“现场条件与记录一致”“记录时效”“红线”
    ok: bool
    detail: str | None = None


class ApprovalSummary(_Model):
    """审批弹窗的内容：由预检结果生成，不透出工具原始参数。"""

    action: str  # 要做什么，如“下发 1201 采空区封闭方案”
    tool: str
    # 要批的内容要点（代码从记录或指令参数取，不经模型改写）：等级与触发条件、方案步骤与密闭巷道、物资缺口、撤人人数等
    facts: list[str] = Field(default_factory=list)
    record_ids: list[str] = Field(default_factory=list)
    checks: list[ApprovalCheck] = Field(default_factory=list)
    human_items: list[str] = Field(default_factory=list)  # 需要确认人把关的人工确认项
    override_reason: str | None = None
    incident_id: str | None = None  # 该动作所属的未关闭火情事件（guard 按引用记录的工作面查；审批超时升级要挂在事件上）


class FlowEvent(_Model):
    """固定流程的阶段事件，写进运行记录；以后转成对外接口的过程事件。"""

    flow: str
    run_id: str
    stage: str
    status: Literal["started", "completed", "failed", "skipped"]
    at: AwareDatetime
    record_ids: list[str] = Field(default_factory=list)
    detail: str | None = None



# ── 接口自描述（rescue-mine，第三版）────────────────────────────────────────


class PanelKind(StrEnum):
    LONGWALL = "longwall"  # 综采工作面（有采空区）
    HEADING = "heading"  # 掘进工作面


class Panel(_Model):
    """一个工作面的拓扑：它的进回风巷、工作面、采空区和避难硐室分别是网络里的哪条巷道/哪个节点。"""

    panel_id: str
    name: str
    kind: PanelKind
    intake_roadway_id: str | None = None
    return_roadway_id: str | None = None
    face_roadway_id: str | None = None
    goaf_roadway_id: str | None = None  # 采空区漏风通道（综采工作面才有）
    refuge_node_id: str | None = None


class MineDescription(_Model):
    """describe_mine 的返回：本矿有哪些工作面、测点用到了哪些角色。"""

    mine_id: str
    name: str
    panels: list[Panel]
    sensor_roles: list[SensorRole]  # 本矿测点实际用到的角色（⊆ SensorRole）


class MineCapabilities(_Model):
    """get_capabilities 的返回：接口版本，以及本矿实际提供哪些工具和功能。"""

    interface_version: str
    contract_version: str
    mine_id: str
    implementation: str  # 实现名，如 mine-sim（模拟矿井）或某矿的适配器
    tools: list[str]
    features: list[str]  # 可选功能：alarms、equipment、notice、dispatch、treatment


# ── 矿井自带报警与设备 ───────────────────────────────────────────────────────


class AlarmLevel(StrEnum):
    WARNING = "warning"  # 预警
    ALARM = "alarm"  # 报警
    CRITICAL = "critical"  # 断电、撤人级别


class AlarmStatus(StrEnum):
    ACTIVE = "active"
    CLEARED = "cleared"  # 已恢复


class MineAlarm(_Model):
    """矿井自己的报警平台里的一条报警（拉取，含确认状态）。不是 fire-assessment 重算的结果。"""

    alarm_id: str
    sensor_id: str | None = None
    measurand: Measurand | None = None
    level: AlarmLevel
    status: AlarmStatus
    raised_at: MineTime
    cleared_at: MineTime | None = None
    value: float | None = None
    threshold: float | None = None
    acknowledged: bool = False
    acknowledged_at: MineTime | None = None
    message: str


class EquipmentKind(StrEnum):
    MAIN_FAN = "main_fan"  # 主通风机
    LOCAL_FAN = "local_fan"  # 局部通风机
    PUMP = "pump"  # 水泵
    NITROGEN_PLANT = "nitrogen_plant"  # 制氮机组
    GROUT_STATION = "grout_station"  # 注浆站
    OTHER = "other"


class EquipmentState(StrEnum):
    RUNNING = "running"
    STANDBY = "standby"
    STOPPED = "stopped"
    FAULT = "fault"


class EquipmentStatus(_Model):
    equipment_id: str
    name: str
    kind: EquipmentKind
    node_id: str | None = None  # 所在网络节点
    state: EquipmentState
    updated_at: MineTime


# ── 通知与指令（只执行，不判断；返回回执）────────────────────────────────────────


class NoticeKind(StrEnum):
    WARNING = "warning"  # 预警广播
    EVACUATION = "evacuation"  # 撤人通知
    INFO = "info"  # 一般提示


class NoticeRequest(_Model):
    kind: NoticeKind
    message: str
    panel: str | None = None
    level: str | None = None  # 预警等级等，原样传递，矿井侧不解释
    targets: list[str] = Field(default_factory=list)  # 人员编号、班组或区域；空表示按工作面广播
    idempotency_key: str  # 相同 key 重复提交只执行一次，返回同一个回执


class DispatchRequest(_Model):
    purpose: str
    lines: list[DispatchLine] = Field(min_length=1)
    idempotency_key: str


class TreatmentRequest(_Model):
    """处置动作：注惰、灌浆、封闭等。矿井侧只执行，不做红线校验（校验在 rescue-agent 的 guard）。"""

    panel: str | None = None  # 共用巷道的动作（如撤人涉及主运输大巷）不属于任何工作面
    type: StepType
    roadways: list[str] = Field(default_factory=list)
    params: dict[str, float | int | str | bool] = Field(default_factory=dict)
    idempotency_key: str


class ActionKind(StrEnum):
    NOTICE = "notice"
    DISPATCH = "dispatch"
    TREATMENT = "treatment"


class ActionStatus(StrEnum):
    ACCEPTED = "accepted"  # 已收到
    EXECUTING = "executing"  # 执行中（真实矿井异步生效）
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"  # 矿井侧拒绝（如设备不可用）


class MineReceipt(_Model):
    """通知、指令的回执。真实矿井异步生效时，用 get_action_status 查后续状态。"""

    action_id: str
    kind: ActionKind
    status: ActionStatus
    received_at: MineTime
    updated_at: MineTime
    idempotency_key: str
    detail: str | None = None
