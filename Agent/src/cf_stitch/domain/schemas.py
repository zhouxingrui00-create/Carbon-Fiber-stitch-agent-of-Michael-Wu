"""Stage-one domain contracts; document claims never become measurements or limits.

Physical unit conversion, equipment feasibility and experimental validation are
intentionally outside this foundation module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Scenario = Literal["general_research", "v_splice", "j_beam", "dual_layer_fixation"]
MeasurementStage = Literal[
    "planned", "during_stitching", "post_stitching", "post_forming",
    "final_testing", "not_applicable", "unspecified",
]
Provenance = Literal[
    "user_provided", "document_source", "experimental_measured",
    "literature_measured", "simulation", "demo", "formula_calculated", "prediction",
]


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, allow_inf_nan=False,
        validate_assignment=True, validate_default=True,
    )


class SourceReference(DomainModel):
    document_id: NonEmptyText
    file_sha256: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$")]
    section: NonEmptyText
    block_id: NonEmptyText
    rendered_page_hint: int | None = Field(default=None, gt=0, strict=True)
    excerpt: NonEmptyText | None = None


class NumericValue(DomainModel):
    """A value with explicit identity, original units and evidence context.

    Missing values carry a reason. Entering a planned setting is not an
    observation. Original values default only to an identical, unconverted copy.
    """

    value: float | None = None
    unit: NonEmptyText
    original_value: float | str | None = None
    original_unit: NonEmptyText | None = None
    measurement_stage: MeasurementStage = "planned"
    value_kind: Literal["setting", "measured", "context", "target", "document_claim", "calculated", "simulation", "prediction", "demo"] = "setting"
    provenance: Provenance = "user_provided"
    source_refs: list[SourceReference] = Field(default_factory=list)
    source_note: NonEmptyText | None = None
    missing_reason: NonEmptyText | None = None

    @field_validator("value", "original_value", mode="before")
    @classmethod
    def reject_booleans(cls, value):
        if isinstance(value, bool):
            raise ValueError("数值不能使用布尔值代替")
        return value

    @model_validator(mode="after")
    def ensure_value_identity(self):
        if self.value is None:
            if not self.missing_reason:
                raise ValueError("缺失值必须保留 missing_reason，不能用 0 代替")
            if self.original_value is not None:
                raise ValueError("当前数值为空时不能同时提供原始数值")
        else:
            if self.missing_reason is not None:
                raise ValueError("已有数值时不能同时声明缺失原因")
            if self.original_value is None:
                object.__setattr__(self, "original_value", self.value)
            if self.original_unit is None:
                object.__setattr__(self, "original_unit", self.unit)
        if self.value_kind == "measured" and self.provenance not in {
            "experimental_measured", "literature_measured"
        }:
            raise ValueError("实测数值必须明确实验或文献实测来源")
        if self.provenance in {"experimental_measured", "literature_measured"} and self.value_kind != "measured":
            raise ValueError("实测来源必须使用 measured，不能混为设定或目标")
        if self.value_kind == "measured" and self.measurement_stage in {"planned", "not_applicable", "unspecified"}:
            raise ValueError("实测数值必须指定实际测量阶段")
        if self.value_kind == "measured" and self.value is not None and not (self.source_refs or self.source_note):
            raise ValueError("实测数值必须保留文献引用或人工来源说明")
        if self.provenance == "document_source" and not self.source_refs:
            raise ValueError("文档来源必须保留可定位引用")
        for kind, provenance in (("prediction", "prediction"), ("demo", "demo"), ("simulation", "simulation"), ("calculated", "formula_calculated")):
            if (self.value_kind == kind) != (self.provenance == provenance):
                raise ValueError(f"{kind} 数值与 {provenance} 来源必须一致，不能混为设定或实测")
        return self

    @classmethod
    def missing(cls, unit: str, reason: str = "尚未提供", *, value_kind: str = "setting") -> "NumericValue":
        return cls(unit=unit, missing_reason=reason, value_kind=value_kind)


class StitchMechanism(str, Enum):
    unstitched = "unstitched"
    lockstitch = "lockstitch"
    chainstitch = "chainstitch"
    tufting = "tufting"
    blind_stitch = "blind_stitch"
    custom_unconfirmed = "custom_unconfirmed"


class MechanismSelection(DomainModel):
    kind: StitchMechanism
    raw_mechanism_name: NonEmptyText | None = None

    @model_validator(mode="after")
    def keep_unconfirmed_mechanisms(self):
        if self.kind == StitchMechanism.custom_unconfirmed and not self.raw_mechanism_name:
            raise ValueError("自定义待确认机构必须保留原始名称")
        raw = (self.raw_mechanism_name or "").replace(" ", "").lower()
        if any(token in raw for token in ("机器人", "robot", "人字形", "herringbone")):
            raise ValueError("机器人属于运动平台，人字形属于路径，不能用作缝合机制名称")
        if "无底线" in raw and self.kind != StitchMechanism.custom_unconfirmed:
            raise ValueError("无底线自锁必须保留 custom_unconfirmed，不能自动映射常规锁式")
        return self


class PathPattern(str, Enum):
    unspecified = "unspecified"
    straight = "straight"
    parallel = "parallel"
    herringbone = "herringbone"
    custom = "custom"


class MotionPlatform(str, Enum):
    unspecified = "unspecified"
    manual = "manual"
    fixed_head = "fixed_head"
    gantry = "gantry"
    robot = "robot"


class MaterialContext(DomainModel):
    material_system: NonEmptyText | None = None
    fiber_grade: NonEmptyText | None = None
    weave: NonEmptyText | None = None
    layup_sequence: NonEmptyText | None = None
    layer_count: int | None = Field(default=None, gt=0, strict=True)
    preform_state: Literal["unknown", "dry", "prepreg"] = "unknown"
    matrix: NonEmptyText | None = None
    compacted_thickness_mm: NumericValue = Field(default_factory=lambda: NumericValue.missing("mm", value_kind="context"))
    uncompacted_thickness_mm: NumericValue = Field(default_factory=lambda: NumericValue.missing("mm", value_kind="context"))
    thickness_condition: NonEmptyText | None = None
    forming_conditions: NonEmptyText | None = None
    post_treatment: NonEmptyText | None = None
    missing_reasons: dict[str, NonEmptyText] = Field(default_factory=dict)

    @field_validator("compacted_thickness_mm", "uncompacted_thickness_mm")
    @classmethod
    def check_thickness(cls, quantity):
        if quantity.unit != "mm":
            raise ValueError("厚度的存储单位必须为 mm；原单位另行保留")
        if quantity.value is not None and quantity.value <= 0:
            raise ValueError("已提供的材料厚度必须大于 0")
        return quantity


class GeometryContext(DomainModel):
    configuration: NonEmptyText | None = None
    backside_accessible: bool | None = None
    region: NonEmptyText | None = None
    fixture_description: NonEmptyText | None = None
    missing_reasons: dict[str, NonEmptyText] = Field(default_factory=dict)


class ProcessParameters(DomainModel):
    """Independent process quantities, without document-derived numeric defaults."""

    pitch_mm: NumericValue = Field(default_factory=lambda: NumericValue.missing("mm"))
    row_spacing_mm: NumericValue = Field(default_factory=lambda: NumericValue.missing("mm"))
    mechanical_needle_spacing_mm: NumericValue = Field(default_factory=lambda: NumericValue.missing("mm"))
    needle_diameter_mm: NumericValue = Field(default_factory=lambda: NumericValue.missing("mm"))
    stitch_frequency_spm: NumericValue = Field(default_factory=lambda: NumericValue.missing("stitches/min"))
    yarn_feed_tension_N: NumericValue = Field(default_factory=lambda: NumericValue.missing("N"))
    yarn_feed_tension_actual_N: NumericValue = Field(default_factory=lambda: NumericValue.missing("N", value_kind="context"))
    tensioner_setting: NumericValue = Field(default_factory=lambda: NumericValue.missing("1"))
    needle_thread_peak_tension_N: NumericValue = Field(default_factory=lambda: NumericValue.missing("N", value_kind="context"))
    residual_yarn_tension_N: NumericValue = Field(default_factory=lambda: NumericValue.missing("N", value_kind="context"))
    upper_thread_tension_N: NumericValue = Field(default_factory=lambda: NumericValue.missing("N"))
    bobbin_thread_tension_N: NumericValue = Field(default_factory=lambda: NumericValue.missing("N"))
    upper_fabric_web_tension_N: NumericValue = Field(default_factory=lambda: NumericValue.missing("N"))
    lower_fabric_web_tension_N: NumericValue = Field(default_factory=lambda: NumericValue.missing("N"))
    needle_depth_mm: NumericValue = Field(default_factory=lambda: NumericValue.missing("mm"))
    foot_lift_mm: NumericValue = Field(default_factory=lambda: NumericValue.missing("mm"))
    needle_normal_angle_deg: NumericValue = Field(default_factory=lambda: NumericValue.missing("deg"))
    line_speed_m_min: NumericValue = Field(default_factory=lambda: NumericValue.missing("m/min"))

    POSITIVE_FIELDS: ClassVar[set[str]] = {
        "pitch_mm", "row_spacing_mm", "mechanical_needle_spacing_mm", "needle_diameter_mm",
        "needle_depth_mm", "stitch_frequency_spm",
    }

    @model_validator(mode="after")
    def validate_units_and_signs(self):
        for name in type(self).model_fields:
            quantity: NumericValue = getattr(self, name)
            expected_unit = (
                "mm" if name.endswith("_mm") else "N" if name.endswith("_N")
                else "stitches/min" if name == "stitch_frequency_spm"
                else "deg" if name.endswith("_deg")
                else "m/min" if name == "line_speed_m_min" else "1"
            )
            if quantity.unit != expected_unit:
                raise ValueError(f"{name} 必须使用 {expected_unit}，原单位可在 original_unit 保留")
            if quantity.value is None:
                continue
            if name in self.POSITIVE_FIELDS and quantity.value <= 0:
                raise ValueError(f"{name} 必须大于 0；未知或未缝合请使用 null 和原因")
            if quantity.value < 0:
                raise ValueError(f"{name} 不能为负值")
        return self


class EquipmentSourceClaim(DomainModel):
    """A source statement, explicitly advisory and unverified."""

    claim_id: str = Field(default_factory=lambda: str(uuid4()))
    field: NonEmptyText
    unit: NonEmptyText
    scope: NonEmptyText
    evidence_kind: Literal["design_specification", "design_parameter", "design_requirement", "initial_trial_window", "qualitative_guidance"]
    min_value: float | None = None
    max_value: float | None = None
    raw_value: NonEmptyText | None = None
    source_refs: list[SourceReference] = Field(min_length=1)
    verification_status: Literal["source_only"] = "source_only"
    enforcement: Literal["advisory"] = "advisory"
    note: NonEmptyText | None = None

    @model_validator(mode="after")
    def check_order(self):
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("来源范围下界不能大于上界；原文冲突应单独保留")
        return self


class ConfirmedParameterLimit(DomainModel):
    field: NonEmptyText
    unit: NonEmptyText
    min_value: float | None = None
    max_value: float | None = None
    applicability: NonEmptyText
    evidence_note: NonEmptyText
    source_refs: list[SourceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_bounds(self):
        if self.min_value is None and self.max_value is None:
            raise ValueError("确认范围至少要有一个边界")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("确认范围下界不能大于上界")
        return self


class ConfirmedEquipmentProfile(DomainModel):
    """Manual review is required even when the source has a numeric specification."""

    equipment_id: str = Field(default_factory=lambda: str(uuid4()))
    equipment_name: NonEmptyText
    approved_by: NonEmptyText
    approved_at: datetime
    approval_reason: NonEmptyText
    limits: list[ConfirmedParameterLimit] = Field(default_factory=list)
    source_claim_ids: list[NonEmptyText] = Field(default_factory=list)
    confirmation_status: Literal["human_confirmed"] = "human_confirmed"

    @field_validator("approved_at")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("人工确认时间必须带时区")
        return value


class Objective(DomainModel):
    metric_name: NonEmptyText
    direction: Literal["maximize", "minimize", "target", "observe"]
    unit: NonEmptyText
    target: NumericValue | None = None
    missing_reason: NonEmptyText | None = "尚未指定目标阈值"
    method_or_standard: NonEmptyText | None = None

    @model_validator(mode="after")
    def target_is_not_measurement(self):
        if self.target is not None:
            if self.target.value_kind != "target":
                raise ValueError("目标阈值必须标注 target，不能使用实测值或预测值")
            if self.target.unit != self.unit:
                raise ValueError("目标与阈值单位必须相同")
            object.__setattr__(self, "missing_reason", None)
        elif not self.missing_reason:
            raise ValueError("未设目标阈值时必须保留原因")
        return self


class MeasurementResult(DomainModel):
    """Result records are separate from task objectives and never auto-created."""

    measurement_id: str = Field(default_factory=lambda: str(uuid4()))
    metric_name: NonEmptyText
    value: float | None
    unit: NonEmptyText
    original_value: float | str | None = None
    original_unit: NonEmptyText | None = None
    measurement_stage: MeasurementStage
    provenance: Literal["experimental_measured", "literature_measured", "simulation", "demo", "prediction"]
    method_or_standard: NonEmptyText | None = None
    loading_direction: NonEmptyText | None = None
    specimen_geometry: NonEmptyText | None = None
    failure_mode: NonEmptyText | None = None
    uncertainty: float | None = Field(default=None, ge=0)
    repeat_count: int | None = Field(default=None, ge=1, strict=True)
    raw_data_ref: NonEmptyText | None = None
    source_refs: list[SourceReference] = Field(default_factory=list)
    source_note: NonEmptyText | None = None
    missing_reason: NonEmptyText | None = None
    namespace: Literal["real", "demo"] = "real"

    @field_validator("value", "original_value", "uncertainty", mode="before")
    @classmethod
    def reject_boolean_measurements(cls, value):
        if isinstance(value, bool):
            raise ValueError("测量数值不能使用布尔值")
        return value

    @model_validator(mode="after")
    def result_is_explicit(self):
        if self.value is None:
            if not self.missing_reason:
                raise ValueError("缺失测量值必须保留原因")
            if self.original_value is not None:
                raise ValueError("缺失测量不能同时包含原始值")
        else:
            if self.missing_reason:
                raise ValueError("已有结果不能同时声明数值缺失")
            if self.original_value is None:
                object.__setattr__(self, "original_value", self.value)
            if self.original_unit is None:
                object.__setattr__(self, "original_unit", self.unit)
            if not (self.raw_data_ref or self.source_refs or self.source_note):
                raise ValueError("已有结果必须明确原始数据、文献引用或人工来源说明")
        if self.provenance in {"experimental_measured", "literature_measured"}:
            if self.measurement_stage in {"planned", "not_applicable", "unspecified"}:
                raise ValueError("实测结果不能使用计划或未指定阶段")
            if self.value is not None and not self.method_or_standard:
                raise ValueError("真实实测结果必须记录测量方法或标准")
        if self.provenance == "literature_measured" and not self.source_refs:
            raise ValueError("文献实测必须有文献定位引用")
        if (self.provenance == "demo") != (self.namespace == "demo"):
            raise ValueError("演示结果必须且只能保存在 demo 空间")
        return self


class ResearchTask(DomainModel):
    task_id: NonEmptyText = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    title: NonEmptyText
    scenario: Scenario = "general_research"
    material: MaterialContext = Field(default_factory=MaterialContext)
    geometry: GeometryContext = Field(default_factory=GeometryContext)
    mechanism: MechanismSelection
    path_pattern: PathPattern = PathPattern.unspecified
    platform: MotionPlatform = MotionPlatform.unspecified
    parameters: ProcessParameters = Field(default_factory=ProcessParameters)
    objectives: list[Objective] = Field(default_factory=list)
    status: Literal["draft"] = "draft"
    namespace: Literal["real", "demo"] = "real"

    @field_validator("created_at")
    @classmethod
    def require_created_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("任务创建时间必须带时区")
        return value

    @model_validator(mode="after")
    def unstitched_has_no_pitch(self):
        if self.mechanism.kind == StitchMechanism.unstitched:
            for name in ("pitch_mm", "row_spacing_mm"):
                if getattr(self.parameters, name).value is not None:
                    raise ValueError("未缝合组的 p/s 不适用，应为 null 并说明原因")
        quantities = [getattr(self.parameters, name) for name in ProcessParameters.model_fields]
        quantities.extend([self.material.compacted_thickness_mm, self.material.uncompacted_thickness_mm])
        quantities.extend(obj.target for obj in self.objectives if obj.target is not None)
        if self.namespace == "real" and any(item.provenance == "demo" for item in quantities):
            raise ValueError("演示数值不能进入真实任务空间")
        return self

    def missing_context(self) -> list[str]:
        """Draft incompleteness is informational, not an invented feasibility verdict."""
        missing = []
        if self.material.material_system is None:
            missing.append("材料体系尚未提供")
        if self.material.compacted_thickness_mm.value is None:
            missing.append("压实厚度及其测量条件尚未提供")
        elif self.material.thickness_condition is None:
            missing.append("厚度测量条件尚未提供")
        if self.geometry.configuration is None:
            missing.append("构型尚未提供")
        if self.geometry.backside_accessible is None:
            missing.append("背面可达性尚未确认")
        if self.mechanism.kind == StitchMechanism.custom_unconfirmed:
            missing.append("自定义缝合机构尚未确认")
        if not self.objectives:
            missing.append("研究目标尚未提供")
        if self.mechanism.kind != StitchMechanism.unstitched:
            for field, label in (("pitch_mm", "沿路径针距 p"), ("row_spacing_mm", "行距 s"), ("needle_diameter_mm", "针径")):
                if getattr(self.parameters, field).value is None:
                    missing.append(f"{label}尚未提供")
        return missing
