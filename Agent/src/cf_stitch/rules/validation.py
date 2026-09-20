"""Pure task, source-window and confirmed-equipment checks.

Source prose is never interpreted as rules. Only explicit typed fields are read.
Passing a numerical check is not a machine-execution authorization.
"""

from __future__ import annotations

from collections import Counter
from math import isfinite
from typing import Literal

from pydantic import Field

from cf_stitch.domain.schemas import (
    ConfirmedEquipmentProfile, DomainModel, NumericValue, ProcessParameters,
    ResearchTask, SourceReference, StitchMechanism,
)
from cf_stitch.knowledge.parameters import build_dictionary
from .results import RuleResult, Status, aggregate_status


POLICY = "PROJECT_SPEC.md §4–6；prompts/03_rules_scenarios.md：字段语义、来源窗口与人工确认限制分开"
LIMIT_FIELDS = {
    "max_stitch_frequency_spm": "stitch_frequency_spm",
    "max_stitch_depth_mm": "needle_depth_mm",
    "max_foot_lift_mm": "foot_lift_mm",
    "stable_sewing_thickness_mm": "compacted_thickness_mm",
    "total_thickness_mm": "compacted_thickness_mm",
}


class ReviewReport(DomainModel):
    status: Status
    findings: list[RuleResult] = Field(default_factory=list)
    candidate_state: Literal["draft", "blocked"] = "draft"
    executable: Literal[False] = False
    note: str = "仅离线审查；通过单项计算不等于整机能力、可达性或避碰认证。"


def _finding(code, status, message, *, field=None, source_refs=(), basis=(), **kwargs):
    return RuleResult(code=code, status=status, message=message, field=field,
                      source_refs=list(source_refs), basis=[POLICY, *basis], **kwargs)


def _task(value) -> ResearchTask:
    # Revalidate even an existing mutable instance; no trusted model_construct path.
    return ResearchTask.model_validate(value.model_dump() if isinstance(value, ResearchTask) else value)


def _values(task: ResearchTask) -> dict[str, NumericValue]:
    return {
        **{name: getattr(task.parameters, name) for name in ProcessParameters.model_fields},
        "compacted_thickness_mm": task.material.compacted_thickness_mm,
        "uncompacted_thickness_mm": task.material.uncompacted_thickness_mm,
    }


def validate_parameter(field: str, quantity: NumericValue | dict) -> RuleResult:
    """Check a single physical field without imposing document windows."""
    try:
        q = NumericValue.model_validate(quantity.model_dump() if isinstance(quantity, NumericValue) else quantity)
        if field in ProcessParameters.model_fields:
            ProcessParameters.model_validate({field: q.model_dump()})
        elif field in {"compacted_thickness_mm", "uncompacted_thickness_mm"}:
            if q.unit != "mm" or (q.value is not None and q.value <= 0):
                raise ValueError("材料厚度须为正数 mm；未知请留空")
        else:
            return _finding("unsupported_field", Status.UNKNOWN, "该字段没有实现独立语义校验，不能用同单位字段替代。", field=field)
    except (ValueError, TypeError) as exc:
        return _finding("invalid_parameter", Status.BLOCK, f"参数结构、数值或单位不合法：{exc}", field=field)
    if q.value is None:
        return _finding("missing_parameter", Status.UNKNOWN, f"参数未提供：{q.missing_reason}", field=field)
    return _finding("parameter_valid", Status.PASS, "数值及规范单位合法；尚未代表设备适配或最优值。", field=field,
                    source_refs=q.source_refs, details={"input": q.model_dump(mode="json")})


def review_source_windows(task: ResearchTask, records: list[dict] | None, *, tension_scope: str | None = None,
                          research_startup: bool | None = None) -> list[RuleResult]:
    """Keep scenario windows independent; source bounds can only warn."""
    if records is None:
        return [_finding("source_unavailable", Status.UNKNOWN, "未提供可审查来源；不补造参数窗口。")]
    try:
        task = _task(task)
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise ValueError("来源须为独立声明对象列表")
        dictionary = build_dictionary(records)
        # Validate reference contracts before even using a numeric statement.
        for record in dictionary:
            for key in ("scope", "field", "unit", "record_id"):
                if not isinstance(record.get(key), str) or not record[key].strip():
                    raise ValueError(f"来源声明缺少 {key}")
            if not record.get("source_refs"):
                raise ValueError("来源声明缺少引用")
            for ref in record["source_refs"]:
                SourceReference.model_validate(ref)
            for key in ("min", "max"):
                value = record.get(key)
                if value is not None and (type(value) not in {int, float} or not isfinite(value)):
                    raise ValueError("来源边界必须是有限数字，不执行文本表达式")
            if record.get("min") is not None and record.get("max") is not None and record["min"] > record["max"]:
                raise ValueError("来源范围上下界倒置，不自动纠正")
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return [_finding("source_invalid", Status.UNKNOWN, f"来源声明不可用于本次审查：{exc}")]
    values = _values(task)
    results = []
    for record in dictionary:
        scope = record["scope"]
        if task.scenario == "general_research":
            use = scope in {"general_research", "research_startup"}
        else:
            use = scope == task.scenario
        if record["field"] == "yarn_feed_tension_N":
            # Explicit yarn conditions can be selected in any engineering
            # scenario without importing D2's unrelated pitch/speed windows.
            use = scope == tension_scope
        if not use:
            continue
        field = record["field"]
        refs = record["source_refs"]
        kind = record["evidence_kind"]
        if field == "max_stitch_frequency_Hz" and task.parameters.stitch_frequency_spm.value is not None:
            results.append(_finding("source_frequency_cycle_unknown", Status.UNKNOWN,
                                    "原声明为 Hz；需明确每周期针数后在单位换算中展示，不直接认定600针/min设备硬限。",
                                    field="stitch_frequency_spm", source_refs=refs,
                                    details={"record_id": record["record_id"], "original_max": record.get("max"), "original_unit": record["unit"]}))
            continue
        if scope == "research_startup" and values.get(field) is not None and values[field].value is not None and research_startup is not True:
            results.append(_finding("startup_scope_unconfirmed", Status.UNKNOWN,
                                    "10–100针/min仅为研发起步窗口；本次是否处于起步试验尚未明确，不作为通用设备限制。",
                                    field=field, source_refs=refs, details={"record_id": record["record_id"], "scope": scope}))
            continue
        if kind not in {"initial_trial_window", "design_specification", "design_parameter", "design_requirement"}:
            continue  # Examples and quality targets are not legal/illegal ranges.
        if field == "fabric_web_tension_N":
            candidates = ["upper_fabric_web_tension_N", "lower_fabric_web_tension_N"]
        else:
            candidates = [LIMIT_FIELDS.get(field, field)]
        for candidate in candidates:
            quantity = values.get(candidate)
            if quantity is None or quantity.value is None:
                continue
            if task.mechanism.kind == StitchMechanism.unstitched and candidate in {"pitch_mm", "row_spacing_mm"}:
                continue
            if quantity.unit != record["unit"]:
                results.append(_finding("source_unit_unresolved", Status.UNKNOWN, "来源与候选单位不同；本次不自动换算或替换物理量。", field=candidate, source_refs=refs))
                continue
            if record.get("semantic_status") or record.get("conflict_group"):
                results.append(_finding("source_pending", Status.UNKNOWN, "该来源定义或版本仍待确认；保留原值，不取交集、不作为硬限。", field=candidate, source_refs=refs, details={"record_id": record["record_id"]}))
                continue
            lower, upper = record.get("min"), record.get("max")
            if lower is None and upper is None:
                continue
            outside = ((lower is not None and quantity.value < lower) or (upper is not None and quantity.value > upper))
            is_window = kind == "initial_trial_window"
            status = Status.WARN if outside or not is_window else Status.PASS
            message = (
                "超出当前场景的来源初始窗口，需研究者审查；不等同数学非法或已确认设备超限。" if outside and is_window
                else "处于当前场景的来源初始窗口内；不是设备能力或工艺质量保证。" if is_window
                else "仅与设备设计声明比较，仍需人工确认实际能力；超出声明。" if outside
                else "仅与设备设计声明比较，仍需人工确认实际能力；数值未超声明。"
            )
            results.append(_finding("source_window" if is_window else "source_design_claim", status, message,
                                    field=candidate, source_refs=refs, details={"record_id": record["record_id"], "scope": scope, "min": lower, "max": upper, "candidate": quantity.value, "unit": quantity.unit, "enforcement": "advisory"}))
            if record.get("requires"):
                results.append(_finding("source_condition_unconfirmed", Status.UNKNOWN,
                                        "数值落入窗口也不能代替来源要求的针线匹配等专项审查。",
                                        field=candidate, source_refs=refs, details={"record_id": record["record_id"], "requires": record["requires"]}))
    if task.parameters.yarn_feed_tension_N.value is not None:
        if tension_scope not in {"general_yarn_exploration", "fine_yarn_low_damage_exploration"}:
            results.append(_finding("tension_scope_missing", Status.UNKNOWN, "请明确通用供纱或细纱低损伤条件；不能合并或取交集。", field="yarn_feed_tension_N"))
    if not dictionary:
        results.append(_finding("source_unavailable", Status.UNKNOWN, "来源列表为空。"))
    return results


def check_equipment(task: ResearchTask | dict, equipment: ConfirmedEquipmentProfile | dict | None, *,
                    applicable_limit_fields: set[str] | None = None,
                    through_thickness: bool | None = None) -> list[RuleResult]:
    """Apply only manually confirmed limits explicitly selected for this task.

No stable-thickness capacity is inferred from foot lift or needle depth. A
separate, explicit through-thickness requirement can compare material thickness
against confirmed penetration depth as a necessary (not sufficient) condition.
"""
    try:
        task = _task(task)
    except (ValueError, TypeError) as exc:
        return [_finding("invalid_task", Status.BLOCK, f"任务结构不合法：{exc}")]
    if equipment is None:
        return [_finding("equipment_unconfirmed", Status.UNKNOWN, "尚未选定人工确认设备限制；只能保留草案。")]
    try:
        raw = equipment.model_dump() if isinstance(equipment, ConfirmedEquipmentProfile) else equipment
        for limit in raw.get("limits", []):
            for bound in ("min_value", "max_value"):
                if isinstance(limit.get(bound), bool):
                    raise ValueError("人工确认边界不能为布尔值")
        equipment = ConfirmedEquipmentProfile.model_validate(raw)
    except (ValueError, TypeError, AttributeError) as exc:
        return [_finding("invalid_equipment", Status.BLOCK, f"人工确认设备结构不合法：{exc}")]
    values = _values(task)
    results = []
    covered = set()
    duplicates = Counter(limit.field for limit in equipment.limits)
    for limit in equipment.limits:
        basis = [f"人工确认：{equipment.equipment_name} / {equipment.equipment_id}；审核人 {equipment.approved_by}；{equipment.approved_at.isoformat()}",
                 f"适用范围：{limit.applicability}；依据：{limit.evidence_note}"]
        detail = {"equipment_id": equipment.equipment_id, "confirmation_field": limit.field, "min": limit.min_value, "max": limit.max_value, "unit": limit.unit}
        if duplicates[limit.field] > 1:
            results.append(_finding("ambiguous_confirmed_limits", Status.UNKNOWN, "同字段存在多个确认范围；不自动择一或取交集。", field=limit.field, source_refs=limit.source_refs, basis=basis))
            continue
        if not applicable_limit_fields or limit.field not in applicable_limit_fields:
            results.append(_finding("limit_scope_unconfirmed", Status.UNKNOWN, "尚未明确确认该设备限制适用于本次材料、机构与测量条件。", field=limit.field, source_refs=limit.source_refs, basis=basis))
            continue
        candidate = LIMIT_FIELDS.get(limit.field, limit.field)
        quantity = values.get(candidate)
        if quantity is None:
            results.append(_finding("limit_semantics_unknown", Status.UNKNOWN, "该限制缺少同语义任务字段；不按单位相同替代其他物理量。", field=limit.field, source_refs=limit.source_refs, basis=basis))
            continue
        if limit.unit != quantity.unit:
            results.append(_finding("limit_unit_mismatch", Status.BLOCK, "确认限制的单位与对应任务字段不一致，需先显式换算并复核。", field=candidate, source_refs=limit.source_refs, basis=basis))
            continue
        if (limit.min_value is not None and limit.min_value < 0) or (limit.max_value is not None and limit.max_value < 0):
            results.append(_finding("invalid_limit_sign", Status.BLOCK, "此物理字段的确认限制不能为负值。", field=candidate, source_refs=limit.source_refs, basis=basis))
            continue
        if quantity.value is None:
            results.append(_finding("equipment_input_missing", Status.UNKNOWN, "已选确认限制，但对应候选值缺失。", field=candidate, source_refs=limit.source_refs, basis=basis))
        else:
            covered.add(candidate)
            outside = ((limit.min_value is not None and quantity.value < limit.min_value)
                       or (limit.max_value is not None and quantity.value > limit.max_value))
            results.append(_finding("equipment_limit_exceeded" if outside else "equipment_limit_pass", Status.BLOCK if outside else Status.PASS,
                                    "超出已确认设备限制，阻断候选批准。" if outside else "仅此字段符合本次选定的人工确认限制。",
                                    field=candidate, source_refs=limit.source_refs, basis=basis, details={**detail, "candidate": quantity.value}))
        if candidate == "needle_depth_mm" and through_thickness is True:
            thickness = task.material.compacted_thickness_mm.value
            if thickness is not None and limit.max_value is not None:
                exceeds = thickness > limit.max_value
                results.append(_finding("through_thickness_depth", Status.BLOCK if exceeds else Status.PASS,
                                        "要求贯穿的试件厚度超过已确认针刺深度；阻断候选批准。" if exceeds else "仅满足贯穿所需深度的必要条件；未证明稳定缝合或可达性。",
                                        field="compacted_thickness_mm", source_refs=limit.source_refs, basis=basis,
                                        assumptions=["本次明确要求贯穿当前压实厚度；该确认深度的测量基准适用"], details={**detail, "thickness_mm": thickness}))
    for field, quantity in values.items():
        if quantity.value is not None and field not in covered:
            results.append(_finding("equipment_coverage_missing", Status.UNKNOWN, "该已填物理量没有适用的同语义确认限制；不能据其他字段批准。", field=field))
    if not equipment.limits:
        results.append(_finding("equipment_limits_missing", Status.UNKNOWN, "人工确认配置没有数值限制，不能判定设备适配。"))
    if task.material.compacted_thickness_mm.value is None:
        results.append(_finding("equipment_thickness_missing", Status.UNKNOWN, "材料压实厚度未提供；无法检查厚度适配。", field="compacted_thickness_mm"))
    return results


def check_spatial_feasibility(task: ResearchTask | dict, context: dict | None = None) -> RuleResult:
    """Context presence does not implement or certify kinematics/collision checks."""
    try:
        task = _task(task)
    except (ValueError, TypeError) as exc:
        return _finding("invalid_task", Status.BLOCK, f"任务结构不合法：{exc}")
    required = ["CAD", "mechanism", "fixture_envelope"]
    if task.platform.value == "robot" or task.scenario == "j_beam":
        required.extend(["robot_model", "TCP_calibration", "tool_load_and_centre_of_gravity"])
    missing = [field for field in required if not (context or {}).get(field)]
    return _finding("spatial_not_verified", Status.UNKNOWN,
                    "可达性与避碰未验证。本阶段没有 CAD/运动学/碰撞求解器，输入齐全也不能给出认证。",
                    details={"missing": missing, "reachability_verified": False, "collision_verified": False})


def validate_task(task: ResearchTask | dict, records: list[dict] | None = None, *,
                  equipment: ConfirmedEquipmentProfile | dict | None = None,
                  applicable_limit_fields: set[str] | None = None,
                  tension_scope: str | None = None, through_thickness: bool | None = None,
                  research_startup: bool | None = None) -> ReviewReport:
    try:
        task = _task(task)
    except (ValueError, TypeError) as exc:
        result = _finding("invalid_task", Status.BLOCK, f"任务结构或参数校验失败：{exc}")
        return ReviewReport(status=Status.BLOCK, findings=[result], candidate_state="blocked")
    findings = [_finding("task_schema", Status.PASS, "任务结构合法；机制、路径、平台及物理量独立。")]
    for missing in task.missing_context():
        findings.append(_finding("context_missing", Status.UNKNOWN, missing))
    for field, quantity in _values(task).items():
        if quantity.value is not None:
            findings.append(validate_parameter(field, quantity))
    if task.mechanism.kind == StitchMechanism.unstitched:
        findings.append(_finding("unstitched_spacing", Status.PASS, "未缝合组 p/s 为空，不执行除法。", field="pitch_mm/row_spacing_mm"))
    findings.extend(review_source_windows(task, records, tension_scope=tension_scope, research_startup=research_startup))
    findings.extend(check_equipment(task, equipment, applicable_limit_fields=applicable_limit_fields, through_thickness=through_thickness))
    if task.scenario in {"v_splice", "j_beam", "dual_layer_fixation"}:
        findings.append(_finding("scenario_review_pending", Status.UNKNOWN,
                                 {"v_splice": "摆动跨距定义与接缝/夹持几何待确认。", "j_beam": "5–15 针/cm 计数定义、无底线自锁机构、R 角/TCP 待确认。", "dual_layer_fixation": "5/6 mm、链式/双线自锁、机械针位间距与空间版本仍待确认；不自动解除。"}[task.scenario],
                                 basis=["docs/SOURCE_AUDIT.md §不可静默合并的问题"]))
    findings.append(check_spatial_feasibility(task))
    status = aggregate_status(findings)
    return ReviewReport(status=status, findings=findings, candidate_state="blocked" if status == Status.BLOCK else "draft")
