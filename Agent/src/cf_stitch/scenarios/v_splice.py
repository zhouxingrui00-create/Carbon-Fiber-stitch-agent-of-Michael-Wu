"""V splice: retain its own research window and independent machine claims."""

from pathlib import Path
from .base import ScenarioSources, build_card, context


def load(root: Path):
    sources = ScenarioSources(root)
    claims = sources.seed_claims((
        "D2-V-P", "D1-V-SWING", "D1-V-FMAX", "D1-V-DEPTH", "D1-V-ERR",
        "D1-V-LIFT", "D1-V-STRENGTH", "D1-V-X", "D1-V-Y", "D1-V-REPEAT",
    ), "v_splice")
    claims.append(sources.declaration(
        "D1-V-WIDTH", "fabric_width_mm", "V形碳布适配幅宽设计声明", "mm", "v_splice",
        "D1:p0270", "600-1000 mm", section="9.2.5", min=600, max=1000,
    ))
    return build_card("v_splice", "V形拼接", claims, [
        context("V_angle_deg", "V形角度", "deg"),
        context("seam_coordinates", "接缝实际坐标与坐标系"),
        context("splice_gap_mm", "对接间隙", "mm"),
        context("edge_distance_mm", "边距", "mm"),
        context("swing_span_mm", "摆动跨距", "mm", note="单侧幅值还是峰峰跨距待确认；不能替代针距。"),
        context("seam_position_offset_mm", "接缝定位偏差", "mm"),
        context("clamping_description", "夹紧方案及包络"),
        context("fabric_web_tension_N", "布面张力", "N", note="不是供纱张力。"),
        context("compacted_thickness_mm", "压实厚度及测量条件", "mm"),
        context("needle_depth_mm", "针刺深度", "mm"),
        context("foot_lift_mm", "压脚抬升高度", "mm", note="原文针脚部件名称待确认。"),
        context("cad_reference", "CAD/夹持包络资料"),
    ], [
        "摆动跨距5–15 mm的幅值/峰峰定义待确认。",
        "原文针脚最大提升高度20 mm的部件名称待确认，不能替代稳定缝合厚度。",
        "强度保留目标≥70%尚无实测；原布/干态接缝/固化构件的状态和测试方法需匹配。",
    ], [
        "沿缝针距3–10 mm是V形场景初始窗口；5–15 mm是独立摆动跨距。",
        "6 mm最大缝合深度、800针/min最高针频分别是设计声明，未确认其联合工况能力。",
        "运动重复精度±0.05 mm与最终缝合精度±0.3 mm分别保留。",
    ])
