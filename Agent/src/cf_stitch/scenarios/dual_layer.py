"""Dual-layer fixation: separate both webs and preserve conflicting designs."""

from pathlib import Path
from .base import ScenarioSources, build_card, context


def load(root: Path):
    sources = ScenarioSources(root)
    claims = sources.seed_claims((
        "D1-DUAL-T", "D1-DUAL-P", "D1-DUAL-F", "D1-DUAL-SPACING",
        "D1-DUAL-ALIGN", "D1-DUAL-H5", "D1-DUAL-H6",
    ), "dual_layer_fixation")
    extra = [
        ("D1-DUAL-SPACE1200", "internal_sewing_space_mm", "内部预留缝合空间描述", "mm", "D1:p0147", "内部预留约1200 mm缝合空间", {"value": 1200, "approximate": True, "conflict_group": "dual_space", "semantic_status": "pending_spatial_definition"}),
        ("D1-DUAL-SPACE1500-2000", "sewing_space_mm", "技术指标缝合空间描述", "mm", "D1:p0188", "缝合空间：1500~2000 mm", {"min": 1500, "max": 2000, "conflict_group": "dual_space", "semantic_status": "pending_spatial_definition"}),
        ("D1-DUAL-WIDTH", "fabric_width_mm", "双层碳布幅宽设计声明", "mm", "D1:p0188", "适配幅宽：600–1000 mm", {"min": 600, "max": 1000}),
        ("D1-DUAL-CHAIN", "raw_mechanism_name", "链式结构描述", "1", "D1:p0147", "缝线在上下层碳布之间形成稳定链式结构。", {"value": "稳定链式结构", "conflict_group": "dual_mechanism", "semantic_status": "pending_actual_mechanism"}),
        ("D1-DUAL-TWO-THREAD", "raw_mechanism_name", "面线/底线自锁描述", "1", "D1:p0166", "缝纫固定采用自锁线迹，而不采用单线链式线迹。自锁线迹由面线和底线通过缠结形成锁结", {"value": "面线和底线自锁线迹；不采用单线链式", "conflict_group": "dual_mechanism", "semantic_status": "pending_actual_mechanism"}),
        ("D1-DUAL-ROTARY-HOOK", "hook_box_description", "旋梭箱结构描述", "1", "D1:p0167", "缝纫机底部设置旋梭箱", {"value": "旋梭箱", "conflict_group": "dual_mechanism", "semantic_status": "pending_actual_mechanism"}),
    ]
    for record_id, field, label, unit, block, excerpt, values in extra:
        section = "9.1.7" if block == "D1:p0188" else "9.1.3 (6)" if block == "D1:p0147" else "9.1.4 (3)"
        claims.append(sources.declaration(record_id, field, label, unit, "dual_layer_fixation", block, excerpt, section=section, **values))
    return build_card("dual_layer_fixation", "双层叠层固定", claims, [
        context("upper_fabric_web_tension_N", "上层布面输送张力", "N"),
        context("lower_fabric_web_tension_N", "下层布面输送张力", "N"),
        context("fabric_web_tension_difference_N", "上层减下层布面张力差", "N", note="派生特征；不能替代两层原始值。"),
        context("upper_line_speed_m_min", "上层布面线速度", "m/min"),
        context("lower_line_speed_m_min", "下层布面线速度", "m/min"),
        context("line_speed_difference_m_min", "上层减下层线速度差", "m/min", note="派生特征；不是针频。"),
        context("epc_deviation_mm", "EPC偏差", "mm"),
        context("roller_setting", "压辊设定及单位"),
        context("presser_force_N", "压脚载荷设定", "N"),
        context("mechanical_needle_spacing_mm", "机械针位间距", "mm", note="100/120/200 mm方向与机构含义待确认，不当作p或s。"),
        context("parallel_track_count", "并行轨迹数", "1"),
        context("total_thickness_mm", "双层总厚度及测量状态", "mm"),
        context("needle_depth_mm", "针刺深度", "mm"),
        context("foot_lift_mm", "压脚抬升高度", "mm"),
        context("fabric_width_mm", "布料实际幅宽", "mm"),
        context("cad_reference", "CAD/夹具与机构包络"),
    ], [
        "双层总厚度5/6 mm并列待确认，不取较小值或较大值作为设备限制。",
        "链式结构与面线/底线自锁、旋梭描述并存，设备版本和成迹机构待确认。",
        "机械多针间距100/120/200 mm的方向、针位/头间距/轨迹间距定义待确认。",
        "内部约1200 mm与1500–2000 mm缝合空间的对象/方向待确认；不等于600–1000 mm布幅。",
    ], [
        "针距10–30 mm及针频100–500针/min是双层固定初始窗口，不裁进D2通用窗口。",
        "上/下层布面张力各自参考20–80 N，需要按克重、幅宽和纤维方向稳定性调试；不是供纱张力。",
        "叠层错位≤0.5 mm是质量目标，不是已测结果或定位重复精度。",
        "并行轨迹数不能直接用于将针数除以机头数推断节拍。",
    ], parameter_bindings=[
        {"field": field, "source_record_id": "D1-DUAL-T", "instance": instance,
         "value": None, "missing_reason": "尚未提供该层的实际设定或测量；窗口不是设定值。"}
        for field, instance in (("upper_fabric_web_tension_N", "upper"), ("lower_fabric_web_tension_N", "lower"))
    ])
