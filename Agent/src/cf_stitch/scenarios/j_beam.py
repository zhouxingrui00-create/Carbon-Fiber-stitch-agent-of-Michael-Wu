"""J beam: local R-corner context and unresolved custom stitch mechanism."""

from pathlib import Path
from .base import ScenarioSources, build_card, context


def load(root: Path):
    sources = ScenarioSources(root)
    claims = sources.seed_claims((
        "D1-J-FMAX", "D1-J-DEPTH", "D1-J-DENSITY", "D1-J-ALIGN", "D1-J-TENSION",
    ), "j_beam")
    declarations = [
        ("D1-J-DIMENSIONS", "head_dimensions_mm", "机头外形尺寸设计声明", "mm", "D1:p0347", "240×100×65mm", {"dimensions": [240, 100, 65]}),
        ("D1-J-MASS", "head_mass_kg", "机头质量设计声明", "kg", "D1:p0348", "~2.5kg", {"value": 2.5, "approximate": True}),
        ("D1-J-POWER", "head_power_W", "机头功率设计声明", "W", "D1:p0349", "200W", {"value": 200}),
    ]
    for record_id, field, label, unit, block, excerpt, values in declarations:
        claims.append(sources.declaration(record_id, field, label, unit, "j_beam", block, excerpt, section="9.3.4", **values))
    arrangement = sources.declaration(
        "D1-J-STATIONS", "station_arrangement", "J型梁工位安排与机构原名", "1", "j_beam",
        "D1:p0297", "拐角1和拐角2处将使用固定缝合头，拐角3处则采用6轴机械臂夹持缝合头进行缝合操作。", section="9.3",
        raw_mechanism_name="小型无底线自锁式缝合头", stitch_mechanism="custom_unconfirmed",
        semantic_status="pending_actual_mechanism",
    )
    claims.append(arrangement)
    arrangements = [
        {"station": position, "motion_platform": platform,
         "raw_mechanism_name": "小型无底线自锁式缝合头", "stitch_mechanism": "custom_unconfirmed",
         "verification_status": "source_only", "enforcement": "advisory",
         "source_refs": arrangement["source_refs"]}
        for position, platform in ((1, "fixed_head"), (2, "fixed_head"), (3, "robot"))
    ]
    return build_card("j_beam", "J型梁R角", claims, [
        context("station", "工位/拐角位置"),
        context("R_radius_mm", "R角半径", "mm"),
        context("local_thickness_mm", "局部厚度与压实/测量条件", "mm"),
        context("needle_normal_angle_deg", "针轴与局部法向夹角", "deg"),
        context("row_spacing_mm", "局部路径行距", "mm"),
        context("TCP_calibration_reference", "TCP标定记录"),
        context("TCP_calibration_error_mm", "TCP标定误差", "mm"),
        context("robot_model", "机器人机型与安装方式"),
        context("load_and_center_of_gravity", "末端载荷和重心"),
        context("fixture_envelope", "夹具/模具空间与包络"),
        context("backside_accessible", "背面成迹空间"),
        context("cad_reference", "CAD资料及坐标系"),
        context("needle_depth_mm", "针刺深度", "mm"),
        context("foot_lift_mm", "压脚抬升高度", "mm"),
    ], [
        "5–15针/cm的计数方向、线迹和实际可调范围待确认；不能与D2针距取交集。",
        "无底线自锁实际成迹、勾线/成环机构及背面空间待确认，保留custom_unconfirmed。",
        "工位安排只是来源方案；实际机型/机构/载荷/TCP/夹具未验证。",
    ], [
        "针密5–15针/cm只在明确均匀单排假设下换算p≈0.667–2 mm；仍待审核且不能自动纳入优化。",
        "10 Hz换算600针/min需要每周期一针；30 mm是缝合深度设计声明，不是已确认稳定缝合厚度。",
        "机头240×100×65 mm、约2.5 kg和200 W不足以证明可达性、机器人负载或避碰。",
        "机器人属于运动平台；固定头和机器人都不能代替缝合机制字段。",
    ], arrangements=arrangements)
