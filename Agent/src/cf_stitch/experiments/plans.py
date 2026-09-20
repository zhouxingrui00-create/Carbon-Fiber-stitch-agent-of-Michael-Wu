"""Source-preserving trial plans. A plan never creates specimens or outcomes."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from cf_stitch.domain.schemas import (
    ConfirmedEquipmentProfile, DomainModel, NonEmptyText, ResearchTask,
    SourceReference, StitchMechanism,
)
from cf_stitch.knowledge.documents import load_extracted_document
from cf_stitch.knowledge.seeds import SeedValidationError, load_experiment_template
from cf_stitch.rules.results import RuleResult, Status, aggregate_status
from cf_stitch.rules.validation import ReviewReport, check_equipment


POLICY = "PROJECT_SPEC.md §8；prompts/04_data_experiments.md：七组待实验、条件待确认、设备适配"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MATERIAL_MATCHING_REQUIREMENT = "各组材料体系与来源需匹配；纱线、铺层、压实、成型及后处理条件保持一致；C0 对照匹配。"
EXPECTED_GROUPS = (
    ("C0", "unstitched", None, None, "基准对照组"),
    ("L1", "lockstitch", 5, 5, "高密度锁式"),
    ("L2", "lockstitch", 5, 10, "中密度锁式"),
    ("L3", "lockstitch", 10, 10, "低密度锁式"),
    ("C1", "chainstitch", 5, 5, "高密度链式"),
    ("C2", "chainstitch", 5, 10, "中密度链式"),
    ("C3", "chainstitch", 10, 10, "低密度链式"),
)
REQUIRED_CONDITIONS = (
    "material_system", "material_batch", "roll_id", "yarn_material", "layup",
    "compaction", "thickness_measurement_condition", "forming_conditions",
    "post_treatment_conditions", "matched_control_criteria",
    "independent_preform_count", "replicates_per_test", "needle_diameter_mm",
    "yarn_feed_tension_N", "stitch_frequency_spm", "line_speed_m_min",
    "method_or_standard", "loading_direction", "equipment_id",
    "randomization_seed", "block_variable",
)
MATCHING_REQUIREMENTS = (
    "compatible equipment for selected thickness and mechanisms",
    "same yarn material", "same layup", "same compaction",
    "same forming and post-treatment", "matched unstitched controls",
    "researcher approves repetition and test plan",
)
STEPS = [
    {"step": 1, "title": "低速穿刺与针线匹配筛查",
     "description": "先确认设备厚度与机构，再由研究者确定低速筛查条件、针线组合和观测方案；不自动填针频或重复数。"},
    {"step": 2, "title": "固定其余条件，比较机制与 p/s",
     "description": "筛查后固定针径、供纱张力、速度及材料/铺层/压实/成型后处理；保留 C0 匹配对照，确认独立制样数、重复数与测试方法后再执行。"},
    {"step": 3, "title": "记录真实运行与试样结果",
     "description": "实验实际发生后再建立运行、父预制体与试样，关联原始文件和测量；本计划不生成试样或标签。"},
]


def _validate_groups(groups: list[dict]) -> None:
    if len(groups) != 7:
        raise ValueError("必须保留 D2 原始七个组别。")
    fields = {"source_group_label", "stitch_mechanism", "pitch_mm", "row_spacing_mm",
              "purpose_zh", "status", "independent_preform_count", "replicates_per_test", "measurements"}
    for group, expected in zip(groups, EXPECTED_GROUPS, strict=True):
        if not isinstance(group, dict) or set(group) != fields:
            raise ValueError("计划组必须保留原模板字段，不能混入试样或测量。")
        observed = tuple(group[name] for name in ("source_group_label", "stitch_mechanism", "pitch_mm", "row_spacing_mm", "purpose_zh"))
        if observed != expected or any(isinstance(group[k], bool) for k in ("pitch_mm", "row_spacing_mm")):
            raise ValueError("七组原始标签、机制及 p/s 不得改写；C0 的 p/s 必须为空。")
        if group["status"] != "planned" or group["measurements"] != []:
            raise ValueError("模板仅允许 planned 且测量结果为空。")
        if group["independent_preform_count"] is not None or group["replicates_per_test"] is not None:
            raise ValueError("模板重复数与独立制样数仍待用户确认。")


class TrialPlan(DomainModel):
    plan_id: NonEmptyText
    title: NonEmptyText
    namespace: Literal["real", "demo"]
    created_at: datetime
    status: Literal["planned"]
    template_id: Literal["D2_THICK_PREFORM_LOCK_CHAIN"]
    source_refs: list[dict] = Field(min_length=1)
    source_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_extraction_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_text: NonEmptyText
    applicability: dict[str, Any]
    groups: list[dict]
    warning: NonEmptyText
    additional_research_design_suggestions: dict[str, Any]
    pending_conditions: dict[str, None]
    missing_fields: list[str]
    stepwise_plan: list[dict]
    measurements: list[Any] = Field(max_length=0)
    executable: Literal[False]
    training_eligible: Literal[False]
    material_matching_requirement: NonEmptyText

    @field_validator("created_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("计划创建时间必须带时区。")
        return value

    @model_validator(mode="after")
    def preserve_pending_source_plan(self):
        _validate_groups(self.groups)
        if self.applicability.get("thickness_example_mm") != [20, 25]:
            raise ValueError("保留 D2 约 20–25 mm 厚件背景，不替换为设备上限。")
        if self.applicability.get("requires") != list(MATCHING_REQUIREMENTS):
            raise ValueError("必须保留模板的匹配条件与设备审查要求。")
        if set(self.pending_conditions) != set(REQUIRED_CONDITIONS) or self.missing_fields != list(REQUIRED_CONDITIONS):
            raise ValueError("待确认条件及缺项清单必须完整；本接口不自动批准或补值。")
        suggestions = self.additional_research_design_suggestions
        if ("randomization_seed" not in suggestions or "block_variable" not in suggestions
                or suggestions["randomization_seed"] is not None or suggestions["block_variable"] is not None):
            raise ValueError("初始计划的随机化种子和区组变量仍待确认，必须保持 null。")
        for ref in self.source_refs:
            parsed = SourceReference.model_validate(ref)
            if parsed.document_id != "D2" or parsed.block_id != "D2:t006":
                raise ValueError("七组模板必须引用 D2:t006。")
        return self


def validate_plan(payload: TrialPlan | dict, *, root: Path | None = None) -> dict:
    """Revalidate schema AND the complete source snapshot before storage.

    The default is this independent project's source package, not the current
    working directory. No caller-provided hash or excerpt is trusted merely
    because its shape is valid. Existing historical plans remain readable; a
    changed/missing package must be reviewed before any new save.
    """
    raw = payload.model_dump(mode="json") if isinstance(payload, TrialPlan) else deepcopy(payload)
    if not isinstance(raw, dict) or raw.get("executable") is not False or raw.get("training_eligible") is not False:
        raise ValueError("待实验模板不可执行，也不是训练数据。")
    validated = TrialPlan.model_validate(raw).model_dump(mode="json")
    expected = _source_plan_fields(PROJECT_ROOT if root is None else Path(root))
    expected.update(stepwise_plan=STEPS, material_matching_requirement=MATERIAL_MATCHING_REQUIREMENT)
    for field, value in expected.items():
        # Compare the unnormalised input too: source text must remain verbatim,
        # not silently whitespace-normalised by the generic Pydantic base.
        if raw[field] != value or validated[field] != value:
            raise SeedValidationError(f"计划字段 {field} 与当前来源快照不一致；拒绝静默修改或保存。")
    return validated


def _source_plan_fields(root: Path) -> dict:
    """Resolve the source once and retain its independently checked identity."""
    root = Path(root)
    template_path = root / "spec" / "experiment_templates.yaml"
    raw = template_path.read_bytes()
    template = deepcopy(load_experiment_template(root))
    _validate_groups(template["groups"])
    snapshot = load_extracted_document(root, "D2")
    block = next((b for b in snapshot.blocks if b.block_id == "D2:t006"), None)
    if block is None:
        raise SeedValidationError("找不到 D2:t006 原始七组表格，不能生成无来源模板。")
    rows = block.cells
    expected_rows = [[label, {"unstitched": "未缝合", "lockstitch": "锁式", "chainstitch": "链式"}[mechanism],
                      "—" if pitch is None else f"{pitch} mm", "—" if spacing is None else f"{spacing} mm", purpose]
                     for label, mechanism, pitch, spacing, purpose in EXPECTED_GROUPS]
    if rows != [["组别", "缝合类型", "针距 p", "行距 s", "用途"], *expected_rows]:
        raise SeedValidationError("D2:t006 与七组模板不一致，请人工核查，未自动覆盖。")
    for ref in template.get("source_refs", []):
        if ref.get("file_sha256") != snapshot.sha256:
            raise SeedValidationError("七组模板引用哈希与来源提取快照不符。")
    if template_path.read_bytes() != raw or load_experiment_template(root) != template:
        raise SeedValidationError("读取期间模板发生变化，请重新读取。")
    return {
        "template_id": template["template_id"], "source_refs": template["source_refs"],
        "source_snapshot_hash": sha256(raw).hexdigest(),
        "source_extraction_sha256": snapshot.extraction_sha256,
        "source_text": block.text, "applicability": template["applicability"],
        "groups": template["groups"], "warning": template["warning"],
        "additional_research_design_suggestions": template["additional_research_design_suggestions"],
    }


def build_trial_plan(root: Path, title: str, *, namespace: str = "real", plan_id: str | None = None) -> dict:
    """Build a source-verified draft without changing any database or source."""
    return validate_plan({
        **_source_plan_fields(root),
        "plan_id": plan_id or str(uuid4()), "title": title, "namespace": namespace,
        "created_at": datetime.now(timezone.utc).isoformat(), "status": "planned",
        "pending_conditions": dict.fromkeys(REQUIRED_CONDITIONS),
        "missing_fields": list(REQUIRED_CONDITIONS), "stepwise_plan": deepcopy(STEPS),
        "measurements": [], "executable": False, "training_eligible": False,
        "material_matching_requirement": MATERIAL_MATCHING_REQUIREMENT,
    }, root=root)


def _result(code, status, message, refs=(), **kwargs):
    return RuleResult(code=code, status=status, message=message, source_refs=list(refs), basis=[POLICY], **kwargs)


def review_plan(plan: TrialPlan | dict, equipment: ConfirmedEquipmentProfile | dict | None = None, *,
                applicable_limit_fields: set[str] | None = None,
                supported_mechanisms: set[str] | list[str] | None = None,
                mechanisms_confirmed: bool = False,
                through_thickness: bool | None = None) -> ReviewReport:
    """Check all six stitched groups at both thickness boundaries, never approve."""
    try:
        plan = validate_plan(plan)
    except OSError as exc:
        return ReviewReport(status=Status.UNKNOWN, findings=[_result("trial_plan_source_unavailable", Status.UNKNOWN, f"计划来源当前不可读取：{exc}")])
    except (TypeError, ValueError) as exc:
        return ReviewReport(status=Status.BLOCK, findings=[_result("invalid_trial_plan", Status.BLOCK, str(exc))], candidate_state="blocked")
    refs = plan["source_refs"]
    findings = [_result("seven_planned_groups", Status.PASS, "七组来源模板结构与空结果校验通过；不是七个独立样本。", refs),
                _result("trial_conditions_pending", Status.UNKNOWN, "重复数、独立制样、针径、张力、速度、材料与测试方案仍待用户确认。", refs,
                        details={"missing_fields": plan["missing_fields"]})]
    confirmed = None
    if equipment is not None:
        try:
            raw = equipment.model_dump() if isinstance(equipment, ConfirmedEquipmentProfile) else equipment
            if any(isinstance(limit.get(bound), bool) for limit in raw.get("limits", []) for bound in ("min_value", "max_value")):
                raise ValueError("设备数值边界不能使用布尔值。")
            confirmed = ConfirmedEquipmentProfile.model_validate(raw)
        except (ValueError, TypeError, AttributeError) as exc:
            findings.append(_result("invalid_equipment", Status.BLOCK, f"设备确认结构不合法：{exc}"))
    if confirmed is None:
        findings.append(_result("equipment_unconfirmed", Status.UNKNOWN, "没有选定人工确认的设备，保留模板，不能批准执行。", refs))
    else:
        for group in plan["groups"]:
            if group["stitch_mechanism"] == "unstitched":
                continue
            for thickness in plan["applicability"]["thickness_example_mm"]:
                task = ResearchTask(title=f"计划 {group['source_group_label']} 厚度端点审查",
                    mechanism={"kind": group["stitch_mechanism"]},
                    material={"compacted_thickness_mm": {"value": thickness, "unit": "mm", "value_kind": "context",
                              "source_note": "模板 20–25 mm 适用背景端点，非试件实测厚度"}},
                    parameters={"pitch_mm": {"value": group["pitch_mm"], "unit": "mm"},
                                "row_spacing_mm": {"value": group["row_spacing_mm"], "unit": "mm"}})
                for finding in check_equipment(task, confirmed, applicable_limit_fields=applicable_limit_fields, through_thickness=through_thickness):
                    finding.details.update(source_group_label=group["source_group_label"], thickness_background_mm=thickness)
                    findings.append(finding)
    if mechanisms_confirmed is not True or confirmed is None or supported_mechanisms is None:
        findings.append(_result("mechanism_capability_unconfirmed", Status.UNKNOWN, "尚未明确人工确认该设备支持锁式与链式；不能用平台或名称推断机构。", refs))
    else:
        allowed = {item.value for item in StitchMechanism}
        valid_support = isinstance(supported_mechanisms, (list, set, tuple)) and all(isinstance(item, str) for item in supported_mechanisms)
        support = set(supported_mechanisms) if valid_support else set()
        if not valid_support or not support <= allowed:
            findings.append(_result("invalid_mechanism_capability", Status.BLOCK, "设备机制含非法名称；机器人与路径均不是线迹机制。"))
        else:
            missing = {"lockstitch", "chainstitch"} - support
            findings.append(_result("mechanism_capability_missing" if missing else "mechanism_capability_pass",
                Status.BLOCK if missing else Status.PASS,
                f"设备已确认机制不含：{', '.join(sorted(missing))}；保留模板，阻断执行。" if missing else "已明确确认两类机制；其他条件仍需独立审查。",
                refs, details={"equipment_id": confirmed.equipment_id, "approved_by": confirmed.approved_by,
                               "approved_at": confirmed.approved_at.isoformat(), "supported_mechanisms": sorted(support)}))
    status = aggregate_status(findings)
    return ReviewReport(status=status, findings=findings, candidate_state="blocked" if status == Status.BLOCK else "draft",
                        note="仅待实验方案审查；不批准上机，不生成运行/试样/测量，不代表训练就绪。")
