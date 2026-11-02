"""fire-model 服务：采空区自燃研判的专业计算。"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

from mcp.server.mcpserver import MCPServer
from rescue_contract.client import MineData
from rescue_contract.models import Measurand, MineTime
from rescue_contract.serving import DEFAULT_PORTS
from starlette.responses import FileResponse, PlainTextResponse

from . import alarms, chart, effect, explosion, trend, tube, warning

INSTRUCTIONS = (
    "火灾专业计算服务。预警等级、指标值、爆炸危险性和实时报警都由确定性计算给出，"
    "引用时照抄数值并注明“计算结果”，不要自行估算或改写。阈值目前为演示占位值。"
)


def build_server(mine_data_target: Any | None = None) -> MCPServer:
    md = MineData(mine_data_target) if mine_data_target is not None else MineData.from_env()
    xc = explosion.load_constants()
    # 下载链接的地址：浏览器能访问到的 fire_model 地址（远程访问网页端时要设成对外地址）
    public_url = (
        os.environ.get("RESCUE_FIRE_MODEL_PUBLIC_URL")
        or f"http://127.0.0.1:{os.environ.get('RESCUE_FIRE_MODEL_PORT', DEFAULT_PORTS['fire_model'])}"
    )
    server = MCPServer("fire_model", instructions=INSTRUCTIONS)

    async def realtime_alarms(panel: str) -> list[dict[str, Any]]:
        found = await alarms.check_methane(md) + await alarms.check_reignition(md, panel)
        return [asdict(a) for a in found]

    @server.tool()
    async def assess_warning_level(panel: str = "1201") -> dict[str, Any]:
        """按标志气体判定工作面采空区自燃预警等级（none/blue/yellow/orange/red），
        返回触发条件、指标值和数据质量说明。可疑数据已排除。
        同时给出自燃发展阶段（stage：no_sign/slow_oxidation/accelerating/intense/unknown）及依据，
        报告阶段时照抄 stage_name，不要根据标志气体自行推断阶段或煤温。

        预警等级反映自燃发展程度；实时红线报警是“立即行动”的触发条件，两者独立判定，
        报警不会抬高等级。realtime_alarms 一并给出同一时刻的报警，有报警时以报警为先。"""
        out = (await warning.assess(md, panel)).to_dict()
        out["realtime_alarms"] = await realtime_alarms(panel)
        return out

    @server.tool()
    async def gas_trend(sensor_id: str, measurand: Measurand, hours: float = 24) -> dict[str, Any]:
        """单个测点某参数的趋势：窗口内首个与最新可用值、变化量（最新减首个）、上升速率（每小时）、数据质量说明。
        报告“升温多少”“上升多少”时照抄 change，不要自己相减。"""
        out = await trend.trend(md, sensor_id, measurand, hours)
        out.pop("hours")
        return out

    @server.tool()
    async def evaluate_effect(sensor_id: str, since: MineTime, measurand: Measurand = Measurand.CO) -> dict[str, Any]:
        """处置效果评估：比较处置开始时刻（since，矿井时间，取自指令台账或指挥人员）前后，
        同一测点同一参数的上升速率，并检查同测点是否新出现乙烯、乙炔。
        只适用于上升即变坏的发火指标（CO、TEMP、C2H4、C2H2、CO2）；O2、CH4、N2 等会报错。
        速率绝对值小于 flat_rate_per_hour 按“不变”处理。
        effect：controlled（已受控）/ continuing（需继续处置）/ worsening（恶化）/ insufficient（观测时间不足）。
        报告效果时照抄 effect_name、速率和 basis，不要自己比较读数下结论。"""
        return await effect.effect_at(md, sensor_id, since, measurand, warning.load_thresholds(), await md.now())

    @server.tool()
    async def evaluate_effect_panel(since: MineTime, panel: str = "1201") -> dict[str, Any]:
        """工作面处置效果的总体评估：对该工作面全部发火指标测点（采空区束管 CO、采空区测温、
        非进风侧安全监控 CO）逐个做 evaluate_effect，给出逐点结果、各结论的测点数和总体结论。
        总体结论：任一测点恶化→恶化；全部已受控→已受控；否则需继续处置；观测时间不足的测点不参与总体结论。
        报告时照抄 overall_name、counts、points，不要自己数测点或汇总；要细看单个测点用 evaluate_effect。
        已观测时长、下次复评时刻照抄 observed_h、next_review_at、observed_h_at_next_review，不要自己推算。"""
        return await effect.panel_effect(md, panel, since)

    def run_triangle(sample: explosion.Sample) -> dict[str, Any]:
        return tube.run_triangle(xc, sample)

    def attach_chart(out: dict[str, Any], label: str, subtitle: str) -> dict[str, Any]:
        return tube.attach_chart(out, xc, public_url, label, subtitle)

    @server.custom_route("/charts/{name}", methods=["GET"])
    async def download_chart(request):
        name = request.path_params["name"]
        path = chart.chart_dir() / name
        if not chart.CHART_NAME.match(name) or not path.is_file():
            return PlainTextResponse("not found", status_code=404)
        media = "image/svg+xml" if name.endswith(".svg") else "application/json"
        return FileResponse(path, media_type=media, filename=chart.download_name(name), headers={"Cache-Control": "no-store"})

    @server.tool()
    async def assess_explosion_risk(sensor_id: str, temperature_sensor_id: str | None = None) -> dict[str, Any]:
        """用测点（束管）最近一次完整的气体分析判定火区混合气体的爆炸危险性（多组分爆炸三角形，含 CO、乙烯等）。
        温度取同一位置的测温点（自动配对）；配不上时会列出候选，用 temperature_sensor_id 指定。
        返回分区 zone（I 爆炸危险区 / II 减风危险区 / III 增风危险区 / IV 相对安全区）、
        explosion_risk（explosive / may_become_explosive / not_explosive，方案 context.explosion_risk 照抄此值）、
        三角形顶点和状态点、所用读数及时刻。报告时照抄 zone_name、meaning 和 warnings，不要自己判断分区。
        chart.markdown 是风险图和下载链接：把它原样单独成段放进最终回答（网页端会显示图片），不要改写路径或链接。"""
        return await tube.assess_tube(md, sensor_id, xc, public_url, temperature_sensor_id)

    @server.tool()
    async def assess_explosion_risk_sample(
        o2_pct: float,
        co2_pct: float,
        temperature_c: float,
        ch4_pct: float | None = None,
        co_ppm: float | None = None,
        c2h4_ppm: float | None = None,
        c2h2_ppm: float | None = None,
        c2h6_ppm: float | None = None,
        h2_ppm: float | None = None,
        n2_pct: float | None = None,
    ) -> dict[str, Any]:
        """用人工取样化验单判定爆炸危险性（没有束管测点或需要复核时用）。数值照化验单填写，注意单位：
        CH4、O2、CO2、N2 为 %，CO、C2H4、C2H2、C2H6、H2 为 ppm；化验单没有的组分不填，不要填估计值。
        O2、CO2 和温度必须是实测值，缺少时不要调用，先向人员索取。返回内容同 assess_explosion_risk。"""
        inputs = {
            "o2_pct": o2_pct, "co2_pct": co2_pct, "temperature_c": temperature_c, "n2_pct": n2_pct,
            "ch4_pct": ch4_pct, "h2_ppm": h2_ppm, "co_ppm": co_ppm, "c2h4_ppm": c2h4_ppm, "c2h2_ppm": c2h2_ppm, "c2h6_ppm": c2h6_ppm,
        }  # fmt: skip
        given = {"CH4": ch4_pct, "H2": h2_ppm, "CO": co_ppm, "C2H4": c2h4_ppm, "C2H2": c2h2_ppm, "C2H6": c2h6_ppm}
        ppm = {"CO", "C2H4", "C2H2", "C2H6", "H2"}
        combustibles = {g: (v / 10_000 if g in ppm else v) for g, v in given.items() if v is not None}
        out = run_triangle(explosion.Sample(combustibles=combustibles, o2=o2_pct, co2=co2_pct, temperature_c=temperature_c, n2=n2_pct))
        missing = [g for g, v in given.items() if v is None]
        if missing:
            out["warnings"].append(f"化验单未提供 {'、'.join(missing)}，未计入；这些组分实际存在时结论偏乐观")
        out["sample"] = {"source": "人工取样化验单", "inputs": {k: v for k, v in inputs.items() if v is not None}}
        return attach_chart(out, "manual-sample", "人工取样化验单")

    @server.tool()
    async def check_realtime_alarms(panel: str = "1201") -> list[dict[str, Any]]:
        """检查实时红线报警：甲烷超 2% 且上升（第75条）、封闭或启封后火区 CO 持续上升的复燃征兆（第164条，
        只对火区台账中的火区检查）。
        有报警时应立即提示指挥人员，不等待其他研判。"""
        return await realtime_alarms(panel)

    return server
