"""Independent scenario modules; no automatic selection or window intersection."""

from pathlib import Path
from . import dual_layer, j_beam, v_splice
from .base import ScenarioConfig, ScenarioEvidenceError, ScenarioSources, build_card, context


SCENARIO_LABELS = {
    "general_research": "通用研发（D2）",
    "v_splice": "V形拼接",
    "j_beam": "J型梁R角",
    "dual_layer_fixation": "双层叠层固定",
}


def get_scenario(root: Path, key: str) -> ScenarioConfig:
    if key == "general_research":
        sources = ScenarioSources(root)
        claims = sources.seed_claims((
            "D2-P", "D2-S", "D2-H", "D2-T", "D2-T-FINE", "D2-YARN", "D2-D",
            "D2-ANGLE", "D2-F", "D2-PIN", "D2-POUT",
        ))
        return build_card(key, SCENARIO_LABELS[key], claims, [
            context("material_system", "材料体系、铺层和成型状态"),
            context("compacted_thickness_mm", "压实厚度及测量条件", "mm"),
            context("needle_thread_compatibility", "针线匹配情况"),
            context("yarn_exploration_scope", "常规供纱或细纱低损伤探索条件"),
            context("backside_accessible", "背面操作空间"),
            context("equipment_profile", "实际人工确认设备配置"),
        ], ["针线匹配、材料状态、实际设备稳定能力和试验条件仍需确认。"], [
            "D2仅给初始试验窗口；常规供纱0.5–10 N与细纱0.1–3 N分别保留适用条件。",
            "薄件1–5 mm和厚件10–30 mm是示例，不能推断5–10 mm非法。",
            "起步针频10–100针/min不是设备最高针频，穿刺/拔针力保持待测。",
        ])
    loaders = {"v_splice": v_splice.load, "j_beam": j_beam.load, "dual_layer_fixation": dual_layer.load}
    if key not in loaders:
        raise ValueError(f"未知场景：{key}")
    return loaders[key](Path(root))


__all__ = ["ScenarioConfig", "ScenarioEvidenceError", "SCENARIO_LABELS", "get_scenario"]
