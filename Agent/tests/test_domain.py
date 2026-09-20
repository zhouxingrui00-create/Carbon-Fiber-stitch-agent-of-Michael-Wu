from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from cf_stitch.domain import (
    ConfirmedEquipmentProfile, ConfirmedParameterLimit, EquipmentSourceClaim,
    MaterialContext, MeasurementResult, MechanismSelection, MotionPlatform,
    NumericValue, Objective, PathPattern, ProcessParameters, ResearchTask,
    SourceReference, StitchMechanism,
)


def source_reference():
    return SourceReference(
        document_id="D2", file_sha256="a" * 64, section="四、关键工艺参数",
        block_id="D2:t002", excerpt="针距 p：3–15 mm",
    )


def task(**kwargs):
    return ResearchTask(title="离线研究草案", mechanism=MechanismSelection(kind="lockstitch"), **kwargs)


@pytest.mark.parametrize("invalid_mechanism", ["robot", "机器人", "herringbone", "人字形"])
def test_platform_and_path_are_not_mechanisms(invalid_mechanism):
    with pytest.raises(ValidationError):
        MechanismSelection(kind=invalid_mechanism)


def test_robot_and_herringbone_are_separate_valid_choices():
    draft = task(platform="robot", path_pattern="herringbone")
    assert draft.mechanism.kind == StitchMechanism.lockstitch
    assert draft.platform == MotionPlatform.robot
    assert draft.path_pattern == PathPattern.herringbone


def test_unconfirmed_self_lock_keeps_original_name():
    selection = MechanismSelection(kind="custom_unconfirmed", raw_mechanism_name="小型无底线自锁式缝合头")
    assert selection.raw_mechanism_name == "小型无底线自锁式缝合头"
    with pytest.raises(ValidationError):
        MechanismSelection(kind="lockstitch", raw_mechanism_name="无底线自锁")
    with pytest.raises(ValidationError):
        MechanismSelection(kind="custom_unconfirmed")


@pytest.mark.parametrize("field", ["pitch_mm", "row_spacing_mm", "mechanical_needle_spacing_mm", "needle_diameter_mm"])
@pytest.mark.parametrize("value", [-1, 0])
def test_nonpositive_geometric_parameters_rejected(field, value):
    with pytest.raises(ValidationError):
        ProcessParameters(**{field: NumericValue(value=value, unit="mm")})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True])
def test_nonfinite_and_boolean_values_rejected(value):
    with pytest.raises(ValidationError):
        NumericValue(value=value, unit="mm")


def test_missing_is_null_and_reason_never_zero():
    quantity = NumericValue.missing("mm")
    assert quantity.value is None
    assert quantity.missing_reason
    with pytest.raises(ValidationError):
        NumericValue(unit="mm")
    with pytest.raises(ValidationError):
        NumericValue(value=2, unit="mm", missing_reason="未知")


def test_unstitched_null_pitch_and_spacing_are_saved():
    draft = ResearchTask(
        title="未缝合对照草案", mechanism=MechanismSelection(kind="unstitched"),
        parameters=ProcessParameters(
            pitch_mm=NumericValue.missing("mm", "未缝合，不适用"),
            row_spacing_mm=NumericValue.missing("mm", "未缝合，不适用"),
        ),
    )
    restored = ResearchTask.model_validate_json(draft.model_dump_json())
    assert restored.parameters.pitch_mm.value is None
    assert restored.parameters.row_spacing_mm.missing_reason == "未缝合，不适用"
    with pytest.raises(ValidationError):
        ResearchTask(title="无效对照", mechanism=MechanismSelection(kind="unstitched"), parameters=ProcessParameters(pitch_mm=NumericValue(value=5, unit="mm")))


def test_draft_without_material_or_parameters_does_not_invent_data():
    draft = task()
    assert draft.status == "draft"
    assert draft.namespace == "real"
    assert draft.created_at.tzinfo is not None
    assert draft.material.material_system is None
    assert draft.objectives == []
    assert all(getattr(draft.parameters, key).value is None for key in ProcessParameters.model_fields)
    assert "measurements" not in draft.model_dump()
    assert draft.missing_context()


def test_initial_document_window_is_not_a_validation_limit():
    # D2's 3–15 mm is a starting window; a draft can record an unapproved 25 mm.
    draft = task(parameters=ProcessParameters(pitch_mm=NumericValue(value=25, unit="mm")))
    assert draft.parameters.pitch_mm.value == 25
    assert draft.parameters.pitch_mm.value_kind == "setting"
    assert draft.parameters.pitch_mm.provenance == "user_provided"


def test_quantities_preserve_original_units_without_implicit_conversion():
    quantity = NumericValue(value=5, unit="mm", original_value=0.5, original_unit="cm")
    assert quantity.original_unit == "cm"
    assert quantity.original_value == 0.5
    assert quantity.value == 5
    with pytest.raises(ValidationError):
        ProcessParameters(pitch_mm=NumericValue(value=0.5, unit="cm"))


def test_all_tensions_and_spacings_remain_independent():
    parameters = ProcessParameters(
        pitch_mm=NumericValue(value=5, unit="mm"),
        row_spacing_mm=NumericValue(value=10, unit="mm"),
        mechanical_needle_spacing_mm=NumericValue(value=120, unit="mm"),
        yarn_feed_tension_N=NumericValue(value=2, unit="N"),
        upper_fabric_web_tension_N=NumericValue(value=30, unit="N"),
        lower_fabric_web_tension_N=NumericValue(value=40, unit="N"),
    )
    assert [parameters.pitch_mm.value, parameters.row_spacing_mm.value, parameters.mechanical_needle_spacing_mm.value] == [5, 10, 120]
    assert [parameters.yarn_feed_tension_N.value, parameters.upper_fabric_web_tension_N.value, parameters.lower_fabric_web_tension_N.value] == [2, 30, 40]
    assert parameters.needle_thread_peak_tension_N.value is None
    assert parameters.yarn_feed_tension_actual_N.value is None


def test_source_claim_cannot_become_hard_limit_or_confirmed_profile():
    claim = EquipmentSourceClaim(
        field="max_stitch_depth_mm", unit="mm", scope="v_splice",
        evidence_kind="design_specification", max_value=6,
        source_refs=[source_reference()],
    )
    assert claim.enforcement == "advisory"
    assert claim.verification_status == "source_only"
    with pytest.raises(ValidationError):
        EquipmentSourceClaim.model_validate({**claim.model_dump(), "enforcement": "hard_limit"})
    with pytest.raises(ValidationError):
        ConfirmedEquipmentProfile.model_validate(claim.model_dump())
    with pytest.raises(ValidationError):
        ConfirmedEquipmentProfile(equipment_name="尚未确认的设备")


def test_human_confirmed_profile_requires_identity_time_reason_and_scope():
    profile = ConfirmedEquipmentProfile(
        equipment_name="测试用配置（非真实能力）", approved_by="测试审核人",
        approved_at=datetime.now(timezone.utc), approval_reason="测试审批记录结构",
        limits=[ConfirmedParameterLimit(field="needle_depth_mm", unit="mm", max_value=2,
            applicability="仅测试夹具", evidence_note="仅供 schema 测试，不导入真实数据库")],
    )
    assert profile.confirmation_status == "human_confirmed"
    with pytest.raises(ValidationError):
        ConfirmedEquipmentProfile(equipment_name="测试", approved_by=" ", approved_at=datetime.now(timezone.utc), approval_reason="原因")
    with pytest.raises(ValidationError):
        ConfirmedEquipmentProfile(equipment_name="测试", approved_by="审核人", approved_at=datetime.now(), approval_reason="原因")


def test_design_target_is_not_measurement():
    objective = Objective(metric_name="strength_retention", direction="maximize", unit="%",
        target=NumericValue(value=70, unit="%", value_kind="target", provenance="document_source", source_refs=[source_reference()]))
    assert objective.target.value_kind == "target"
    with pytest.raises(ValidationError):
        MeasurementResult.model_validate(objective.model_dump())
    with pytest.raises(ValidationError):
        NumericValue(value=70, unit="%", value_kind="measured", provenance="document_source", source_refs=[source_reference()])


def test_real_measurement_needs_explicit_provenance_and_method():
    with pytest.raises(ValidationError):
        MeasurementResult(metric_name="test_only", value=1, unit="N", measurement_stage="final_testing", provenance="experimental_measured")
    with pytest.raises(ValidationError):
        MeasurementResult(metric_name="test_only", value=1, unit="N", measurement_stage="planned", provenance="experimental_measured", source_note="fixture", method_or_standard="fixture")


def test_demo_results_cannot_enter_real_namespace():
    with pytest.raises(ValidationError):
        MeasurementResult(metric_name="test_only", value=1, unit="N", measurement_stage="final_testing", provenance="demo", source_note="test fixture")
    demo = MeasurementResult(metric_name="test_only", value=1, unit="N", measurement_stage="final_testing", provenance="demo", source_note="test fixture", namespace="demo")
    assert demo.provenance == "demo"
    with pytest.raises(ValidationError):
        task(parameters=ProcessParameters(pitch_mm=NumericValue(value=5, unit="mm", value_kind="demo", provenance="demo")))


@pytest.mark.parametrize("provenance", ["prediction", "demo", "simulation", "formula_calculated"])
def test_computed_and_synthetic_provenance_cannot_masquerade_as_setting(provenance):
    with pytest.raises(ValidationError):
        NumericValue(value=5, unit="mm", value_kind="setting", provenance=provenance)


def test_measured_quantity_requires_a_source_and_keeps_the_observation_stage():
    with pytest.raises(ValidationError):
        NumericValue(value=2, unit="N", value_kind="measured", provenance="experimental_measured", measurement_stage="during_stitching")
    observation = NumericValue(value=2, unit="N", value_kind="measured", provenance="experimental_measured", measurement_stage="during_stitching", source_note="schema测试夹具，不导入真实数据")
    assert observation.measurement_stage == "during_stitching"


def test_unknown_fields_empty_title_and_invalid_thickness_are_rejected():
    with pytest.raises(ValidationError):
        task(prediction=100)
    with pytest.raises(ValidationError):
        ResearchTask(title=" ", mechanism=MechanismSelection(kind="lockstitch"))
    with pytest.raises(ValidationError):
        MaterialContext(compacted_thickness_mm=NumericValue(value=-1, unit="mm", value_kind="context"))
    with pytest.raises(ValidationError):
        SourceReference(document_id="D2", file_sha256="not-a-hash", section="s", block_id="b")
