"""Flat interchange contract; settings and observations keep distinct identities."""

from __future__ import annotations

from math import isclose
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from cf_stitch.domain.schemas import (
    DomainModel, MeasurementStage, MechanismSelection, MotionPlatform, PathPattern,
    StitchMechanism, NonEmptyText,
)

DataProvenance = Literal["experimental_measured", "literature_measured", "simulation", "demo", "prediction"]
PROVENANCES = ("experimental_measured", "literature_measured", "simulation", "demo", "prediction")
Positive = Annotated[float, Field(gt=0)]
Nonnegative = Annotated[float, Field(ge=0)]
Hash = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class ObservationRow(DomainModel):
    """One observation, or an explicit missing observation, never a plan replicate.

    All *_setting_* fields are pre-process settings. `value` is the separately
    identified measured/simulated/demo/predicted outcome specified by metric_name.
    A null value is not a label and carries missing_reason.
    """

    measurement_id: NonEmptyText
    source_id: NonEmptyText
    provenance: DataProvenance
    namespace: Literal["real", "demo"] = "real"
    source_citation: NonEmptyText
    literature_record_id: NonEmptyText | None = None
    aggregation_level: Literal["individual", "aggregate"] = "individual"
    reported_sample_size: int | None = Field(default=None, gt=0, strict=True)

    material_id: NonEmptyText
    material_system: NonEmptyText | None = None
    material_batch: NonEmptyText | None = None
    roll_id: NonEmptyText | None = None
    layup_sequence: NonEmptyText | None = None
    forming_conditions: NonEmptyText | None = None
    post_treatment: NonEmptyText | None = None
    parent_preform_id: NonEmptyText | None = None
    specimen_id: NonEmptyText | None = None
    specimen_origin: NonEmptyText | None = None
    specimen_geometry: NonEmptyText | None = None
    failure_mode: NonEmptyText | None = None
    run_id: NonEmptyText | None = None
    run_segment: NonEmptyText | None = None
    plan_id: NonEmptyText | None = None
    group_label: NonEmptyText | None = None
    control_specimen_id: NonEmptyText | None = None
    equipment_id: NonEmptyText | None = None
    equipment_name: NonEmptyText | None = None
    stitch_mechanism: StitchMechanism | None = None
    raw_mechanism_name: NonEmptyText | None = None
    path_pattern: PathPattern = PathPattern.unspecified
    motion_platform: MotionPlatform = MotionPlatform.unspecified
    compacted_thickness_mm: Positive | None = None
    thickness_condition: NonEmptyText | None = None
    pitch_setting_mm: Positive | None = None
    row_spacing_setting_mm: Positive | None = None
    mechanical_needle_spacing_setting_mm: Positive | None = None
    needle_diameter_setting_mm: Positive | None = None
    yarn_feed_tension_setting_N: Nonnegative | None = None
    upper_fabric_web_tension_setting_N: Nonnegative | None = None
    lower_fabric_web_tension_setting_N: Nonnegative | None = None
    stitch_frequency_setting_spm: Positive | None = None
    needle_depth_setting_mm: Positive | None = None
    foot_lift_setting_mm: Nonnegative | None = None
    line_speed_setting_m_min: Nonnegative | None = None

    metric_name: NonEmptyText
    value: float | None = None
    unit: NonEmptyText | None = None
    measurement_stage: MeasurementStage
    test_method: NonEmptyText | None = None
    loading_direction: NonEmptyText | None = None
    missing_reason: NonEmptyText | None = None
    uncertainty: Nonnegative | None = None
    uncertainty_method: NonEmptyText | None = None
    metric_kind: Literal["scalar", "count_rate"] = "scalar"
    numerator: Nonnegative | None = None
    denominator: Positive | None = None
    observation_window: NonEmptyText | None = None
    raw_data_ref: NonEmptyText | None = None
    raw_data_sha256: Hash | None = None

    @field_validator("*", mode="before")
    @classmethod
    def reject_numeric_booleans(cls, value, info):
        numeric = {
            "value", "reported_sample_size", "uncertainty", "numerator", "denominator",
            "compacted_thickness_mm",
        }
        if isinstance(value, bool) and (info.field_name in numeric or "_setting_" in info.field_name):
            raise ValueError("数值不能使用布尔值")
        return value

    @model_validator(mode="after")
    def validate_identity(self):
        if (self.provenance == "demo") != (self.namespace == "demo"):
            raise ValueError("demo 必须且只能进入 demo 空间；其他来源使用 real 空间")
        if self.stitch_mechanism is not None:
            MechanismSelection(kind=self.stitch_mechanism, raw_mechanism_name=self.raw_mechanism_name)
        elif self.raw_mechanism_name is not None:
            raise ValueError("提供原机构名称时必须显式指定机制")
        if self.stitch_mechanism == StitchMechanism.unstitched and (
            self.pitch_setting_mm is not None or self.row_spacing_setting_mm is not None
        ):
            raise ValueError("未缝合组 p/s 必须为 null")
        if self.value is None:
            if not self.missing_reason:
                raise ValueError("缺失测量必须填写 missing_reason，不能用 0 代替")
            if self.uncertainty is not None:
                raise ValueError("缺失测量不能有数值不确定度")
        else:
            if self.missing_reason is not None:
                raise ValueError("已有数值不能同时声明缺失原因")
            if not self.unit or self.unit.casefold() in {"unknown", "未知", "?", "待确认"}:
                raise ValueError("已有数值必须保留明确单位；未知单位不能自动合并")
            if self.measurement_stage in {"planned", "unspecified", "not_applicable"}:
                raise ValueError("已有结果必须指定过程或测试阶段，不能当作设定值")
        if self.uncertainty is not None and not self.uncertainty_method:
            raise ValueError("不确定度必须说明定义或方法，不能当作工程合格概率")
        if self.metric_kind == "count_rate":
            if self.value is not None and (self.numerator is None or self.denominator is None or not self.observation_window):
                raise ValueError("计数率必须保存分子、分母和观察窗口")
            if any(v is not None and not float(v).is_integer() for v in (self.numerator, self.denominator)):
                raise ValueError("计数率分子与分母是事件计数，必须为整数")
            if self.value is not None:
                if self.unit not in {"1", "%"}:
                    raise ValueError("计数率单位必须明确为 1 或 %")
                expected = self.numerator / self.denominator * (100 if self.unit == "%" else 1)
                if not isclose(self.value, expected, rel_tol=1e-6, abs_tol=1e-9):
                    raise ValueError("计数率与分子/分母不一致（相对容差 1e-6，绝对容差 1e-9）；不自动改写数值")
        elif any(item is not None for item in (self.numerator, self.denominator, self.observation_window)):
            raise ValueError("分子/分母/观察窗口仅适用于 count_rate")
        if self.provenance == "literature_measured":
            if not self.literature_record_id:
                raise ValueError("文献实测必须有唯一原始记录定位 literature_record_id")
            if self.aggregation_level == "aggregate" and any(
                item is not None for item in (self.specimen_id, self.parent_preform_id, self.run_id, self.control_specimen_id)
            ):
                raise ValueError("文献均值不得冒充独立试样、父预制体或运行记录")
        elif self.literature_record_id is not None:
            raise ValueError("literature_record_id 仅用于文献实测")
        if self.aggregation_level == "individual":
            if self.reported_sample_size not in (None, 1):
                raise ValueError("个体记录不能使用多试样均值重复数")
            if self.provenance == "experimental_measured" and not all(
                (self.specimen_id, self.parent_preform_id, self.run_id, self.specimen_origin)
            ):
                raise ValueError("实验个体必须关联试样、父预制体、运行和试样来源")
        elif any(item is not None for item in (self.specimen_id, self.parent_preform_id, self.run_id, self.control_specimen_id)):
            raise ValueError("聚合结果不得冒充单独物理试样、父预制体或运行记录")
        if self.control_specimen_id and self.control_specimen_id == self.specimen_id:
            raise ValueError("试样不能以自身作为对照")
        if self.equipment_name is not None and self.equipment_id is None:
            raise ValueError("设备名称必须关联 equipment_id；记录不代表确认设备能力")
        if self.raw_data_sha256 and not self.raw_data_ref:
            raise ValueError("原始文件声明哈希必须关联 raw_data_ref")
        return self


def validate_rows(rows: list[ObservationRow | dict], namespace: str = "real") -> dict:
    """Pure schema validation; never allocates an entity or inserts a measurement."""
    if namespace not in ("real", "demo"):
        raise ValueError("数据空间只能为 real 或 demo")
    if not isinstance(rows, list):
        raise ValueError("导入记录必须是列表")
    from pydantic import ValidationError

    clean, errors, warnings = [], [], []
    for number, row in enumerate(rows, 1):
        try:
            raw = row.model_dump(mode="python") if isinstance(row, ObservationRow) else row
            validated = ObservationRow.model_validate(raw)
            if validated.namespace != namespace:
                raise ValueError("行命名空间与导入空间不一致")
            clean.append(validated.model_dump(mode="json"))
            missing = [key for key in ("material_system", "material_batch", "roll_id", "test_method", "loading_direction", "post_treatment") if getattr(validated, key) is None]
            if missing:
                warnings.append({"row": number, "field": ",".join(missing), "message": "保留未知上下文为 null；不得视为匹配条件或训练资格"})
        except ValidationError as exc:
            errors.extend({"row": number, "field": ".".join(str(v) for v in e["loc"]), "message": e["msg"]} for e in exc.errors())
        except (ValueError, TypeError) as exc:
            errors.append({"row": number, "field": "", "message": str(exc)})
    if not rows:
        errors.append({"row": 0, "field": "", "message": "没有可导入记录"})
    return {"valid": not errors, "rows": clean, "errors": errors, "warnings": warnings, "duplicates": []}
