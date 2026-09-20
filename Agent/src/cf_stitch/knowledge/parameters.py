"""Read-only parameter semantics and explicit, independent human confirmations.

No source text is interpreted as code, configuration, or approval. The dictionary
adds navigation metadata to copies of the immutable source records. In particular,
it never intersects ranges or promotes recommendations into equipment limits.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
import unicodedata
from typing import Iterable, Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from cf_stitch.domain.schemas import DomainModel, NonEmptyText, SourceReference


class EvidenceCategory(str, Enum):
    initial_trial_window = "initial_trial_window"
    design_specification = "design_specification"
    design_target = "design_target"
    confirmed_equipment_limit = "confirmed_equipment_limit"
    experimental_measured = "experimental_measured"
    derived = "derived"
    prediction = "prediction"
    descriptive_ranges = "descriptive_ranges"
    example_values = "example_values"
    qualitative_guidance = "qualitative_guidance"
    measurement_requirement = "measurement_requirement"


CATEGORY_LABELS = {
    "initial_trial_window": "初始试验窗口",
    "design_specification": "设备设计规格 / 设计要求",
    "design_target": "质量目标",
    "confirmed_equipment_limit": "人工确认限制",
    "experimental_measured": "实验实测值",
    "derived": "公式结果",
    "prediction": "模型预测",
    "descriptive_ranges": "描述性示例范围",
    "example_values": "规格示例",
    "qualitative_guidance": "定性指导",
    "measurement_requirement": "待测项目（没有实测值）",
}

# This map is application policy, not a rule obtained from document text.
_SOURCE_CATEGORY = {
    "initial_trial_window": "initial_trial_window",
    "design_specification": "design_specification",
    "design_requirement": "design_specification",
    "design_parameter": "design_specification",
    "design_target": "design_target",
    "descriptive_ranges": "descriptive_ranges",
    "example_values": "example_values",
    "qualitative_guidance": "qualitative_guidance",
    "must_measure": "measurement_requirement",
}

_FIELD_SEMANTICS = {
    "pitch_mm": ("along_path_pitch", "沿缝合路径相邻穿刺点的针距 p", ["针距", "沿路径", "p"]),
    "row_spacing_mm": ("between_rows_spacing", "相邻缝合行之间的行距 s，与沿路径针距 p 分开", ["针距", "行距", "p/s", "s"]),
    "mechanical_needle_spacing_mm": ("mechanical_needle_spacing", "机械针位间距；原文多针针距的方向和机构含义待确认", ["针距", "机械间距", "针位间距", "头间距"]),
    "linear_stitch_density_per_cm": ("linear_stitch_density", "每厘米针数，计数方向与机构待确认；不能直接当作 mm 针距", ["针距", "针密", "针/cm"]),
    "yarn_feed_tension_N": ("yarn_feed_tension", "供纱张力；设定、实测及测量位置需分列", ["张力", "供纱"]),
    "fabric_web_tension_N": ("fabric_web_tension", "上下层布面输送张力，分别记录 upper/lower；不是供纱张力", ["张力", "布面", "布料", "输送"]),
    "fabric_tension_fluctuation_abs_N": ("fabric_tension_fluctuation", "布料张力波动目标的绝对幅值；不是供纱设定值", ["张力", "布面", "布料", "波动"]),
    "stitch_frequency_spm": ("stitch_frequency", "单位时间针数；不是线速度，也不是设备最高针频", ["针频", "针/min", "速度"]),
    "max_stitch_frequency_spm": ("maximum_stitch_frequency_claim", "来源声明的最高设计针频，不是经设备确认的稳定运行范围", ["针频", "最高", "针/min"]),
    "max_stitch_frequency_Hz": ("maximum_stitch_frequency_claim", "保留原始 Hz 及种子中的条件换算说明；每周期一针仍需确认", ["针频", "Hz", "最高"]),
    "compacted_thickness_mm": ("material_thickness", "材料厚度及压实/测量状态；示例间的 5–10 mm 不是非法区间", ["厚度", "薄件", "厚件"]),
    "total_thickness_mm": ("total_material_thickness_claim", "双层工件总厚度设计声明；5/6 mm 两个来源版本并存", ["厚度", "双层"]),
    "max_stitch_depth_mm": ("needle_depth_claim", "设计缝合深度声明，不代替工件厚度或已验证的稳定缝合厚度", ["深度", "针刺"]),
    "max_foot_lift_mm": ("foot_lift_claim", "抬升高度声明；原文部件名称待确认，不代替针刺深度", ["压脚", "抬升"]),
    "penetration_peak_force_N": ("penetration_force_to_measure", "待实测穿刺峰值力，当前没有测量结果", ["穿刺力", "测量"]),
    "withdrawal_peak_force_N": ("withdrawal_force_to_measure", "待实测拔针峰值力，当前没有测量结果", ["拔针力", "测量"]),
}


def build_dictionary(records: Iterable[dict]) -> list[dict]:
    """Add classification to independent copies without changing source metadata.

    Only known document-statement kinds are accepted. Measured, predicted, and
    confirmed records require their own explicit workflows; text claiming such
    status cannot enter through this source-dictionary function.
    """
    result = []
    for original in records:
        record = deepcopy(original)
        kind = record.get("evidence_kind")
        if kind not in _SOURCE_CATEGORY:
            raise ValueError(f"不支持的来源声明类型：{kind!r}；不能从文档自动确认或生成实测/预测")
        if record.get("verification_status") != "source_only" or record.get("enforcement") != "advisory":
            raise ValueError("参数词典只接收 source_only / advisory 的独立来源声明")
        category = _SOURCE_CATEGORY[kind]
        field = record["field"]
        group, explanation, aliases = _FIELD_SEMANTICS.get(
            field, (field, "按原始字段、单位与适用场景分别保存", [])
        )
        record.update(
            category=category, category_label=CATEGORY_LABELS[category],
            semantic_group=group, semantic_explanation=explanation,
            search_aliases=list(aliases),
        )
        # Existing seed annotations are explicitly derived quantities, distinct
        # from the source's original 5–15 stitches/cm or 10 Hz declaration. No
        # numerical computation or equipment validation is performed here.
        annotations = []
        if "derived_pitch_range_mm" in record:
            annotations.append({
                "category": "derived", "label": "种子已记录的条件推导（不是文档针距原值）",
                "field": "pitch_mm", "unit": "mm",
                "range": deepcopy(record["derived_pitch_range_mm"]),
                "assumptions": record.get("derivation"),
                "source_record_id": record["record_id"], "seed_value_field": "derived_pitch_range_mm",
                "is_measured": False, "is_equipment_confirmed": False,
                "status": "assumptions_unconfirmed", "enforcement": "advisory",
            })
        if "canonical_max" in record:
            annotations.append({
                "category": "derived", "label": "种子已记录的条件单位换算（保留原单位）",
                "field": record.get("canonical_field"), "max": record["canonical_max"],
                "unit": "stitches/min" if record.get("canonical_field") == "max_stitch_frequency_spm" else None,
                "assumptions": record.get("conversion"),
                "source_record_id": record["record_id"], "seed_value_field": "canonical_max",
                "is_measured": False, "is_equipment_confirmed": False,
                "status": "assumptions_unconfirmed", "enforcement": "advisory",
            })
        record["derived_annotations"] = annotations
        result.append(record)
    return result


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def search_dictionary(records: Iterable[dict], query: str) -> list[dict]:
    """Literal local keyword matching; whitespace-separated terms use AND.

    Aliases help discover related spacing fields but never combine their values.
    Empty queries list the dictionary; unknown terms return an empty list.
    """
    if not isinstance(query, str):
        raise TypeError("查询必须为文本")
    terms = _normalized(query).split()
    return [record for record in build_dictionary(records)
            if all(term in _normalized(json.dumps(record, ensure_ascii=False)) for term in terms)]


class ManualParameterConfirmation(DomainModel):
    """A new human-authored version; never an in-place update of a source claim.

    Approval is scoped to an identified piece of equipment and stated conditions.
    Creating it is neither a measurement nor a resolution of all source conflicts.
    """

    confirmation_id: NonEmptyText = Field(default_factory=lambda: str(uuid4()))
    source_record_id: NonEmptyText
    field: NonEmptyText
    unit: NonEmptyText
    equipment_name: NonEmptyText
    review_scope: NonEmptyText
    min_value: float | None = None
    max_value: float | None = None
    reviewer: NonEmptyText
    reason: NonEmptyText
    confirmed_at: datetime
    source_refs: list[SourceReference] = Field(min_length=1)
    category: Literal["confirmed_equipment_limit"] = "confirmed_equipment_limit"
    namespace: Literal["real", "demo"] = "real"

    @field_validator("min_value", "max_value", mode="before")
    @classmethod
    def reject_boolean_limits(cls, value):
        if isinstance(value, bool):
            raise ValueError("限制数值不能使用布尔值代替")
        return value

    @field_validator("confirmed_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("人工确认时间必须带时区")
        return value

    @model_validator(mode="after")
    def valid_limits(self):
        if self.min_value is None and self.max_value is None:
            raise ValueError("人工确认限制至少需要一个有限边界")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("人工确认下界不能高于上界；不能静默合并来源范围")
        return self


# (document, block, section, exact literal excerpt). These are review questions,
# not instructions taken from sources. A reference is emitted only if the exact
# text is found in the identified packaged source version.
_PENDING_SPECS = (
    ("j_beam_density", "J 型梁 5–15 针/cm 与通用针距", "j_beam",
     "确认线迹、针数计数方向、单位及实际可调范围；保留两个场景，不取交集或覆盖。",
     ("D1-J-DENSITY", "D2-P"), (
         ("D1", "D1:p0335", "9.3.3", "缝合针密：缝合处的针密可控，5~15针/cm，确保缝合部分的均匀平整。"),
         ("D2", "D2:t002", "四、关键工艺参数", "3–15 mm；典型 5、10 mm"))),
    ("dual_thickness", "双层总厚度 ≤5 / ≤6 mm", "dual_layer_fixation",
     "确认设备版本、厚度测量/压实条件及对应工件；5 和 6 mm 均保留为来源声明。",
     ("D1-DUAL-H5", "D1-DUAL-H6"), (
         ("D1", "D1:p0120", "9.1.1", "双层缝合总厚度≤5 mm"),
         ("D1", "D1:p0188", "9.1.7", "双层总厚度：≤6 mm"))),
    ("dual_mechanism", "稳定链式与面线 / 底线自锁描述", "dual_layer_fixation",
     "确认设备版本及真实成迹机构；未确认前不推断底线需求或给出机构特定工艺建议。",
     (), (
         ("D1", "D1:p0147", "9.1.3 (6)", "缝线在上下层碳布之间形成稳定链式结构。"),
         ("D1", "D1:p0166", "9.1.4 (3)", "缝纫固定采用自锁线迹，而不采用单线链式线迹。自锁线迹由面线和底线通过缠结形成锁结"),
         ("D1", "D1:p0167", "9.1.4 (3)", "缝纫机底部设置旋梭箱、伺服驱动器、针板、X/Y轴滑台和支撑台。"))),
    ("mechanical_spacing", "多针针距 100 / 120 / 200 mm 的空间含义", "dual_layer_fixation",
     "确认方向、针位/机头布置与轨迹数量；机械针位间距独立于沿路径 p 和行距 s。",
     ("D1-DUAL-SPACING", "D1-DUAL-P", "D2-P", "D2-S"), (
         ("D1", "D1:p0147", "9.1.3 (6)", "多针针距可根据缝合要求设计为100 mm、120 mm、200 mm或可调式结构"),
         ("D2", "D2:t002", "四、关键工艺参数", "针距 p"),
         ("D2", "D2:t002", "四、关键工艺参数", "行距 s"))),
    ("swing_span", "摆动跨距的幅值定义", "v_splice",
     "确认 5–15 mm 是单侧幅值、峰峰跨距还是其他定义；确认前不自动换算真实针位。",
     ("D1-V-SWING",), (
         ("D1", "D1:p0253", "9.2.3 (4)", "摆动跨距5-15mm。"),)),
    ("no_bobbin_self_lock", "无底线自锁的实际机构", "j_beam",
     "保留 custom_unconfirmed 及原机构名称；确认勾线/成环原理、背面操作空间，不能自动归为常规双线锁式。",
     (), (
         ("D1", "D1:p0297", "9.3.2", "a小型无底线自锁式缝合头：用于在拐角处进行布料的缝合。"),
         ("D2", "D2:p0009", "二、主要缝合技术类型 1.锁式缝合", "采用针线与底线在预制体内部或背面交织形成稳定线迹，通常需要旋梭、摆梭或其他底线成迹机构。"))),
)


def _package_versions(root: Path) -> tuple[dict, dict]:
    documents, problems = {}, {}
    try:
        manifest = json.loads((root / "sources" / "manifest.json").read_text(encoding="utf-8-sig"))
        identity = {item["document_id"]: item["sha256"] for item in manifest}
    except (OSError, ValueError, TypeError, KeyError):
        return {}, {"D1": "来源清单不可用", "D2": "来源清单不可用"}
    for document_id in ("D1", "D2"):
        try:
            data = json.loads((root / "sources" / "extracted" / f"{document_id}_text_blocks.json").read_text(encoding="utf-8-sig"))
            metadata = data["metadata"]
            if metadata["document_id"] != document_id or metadata["sha256"] != identity[document_id]:
                raise ValueError("来源身份不一致")
            # SourceReference validates SHA syntax without inventing a substitute.
            SourceReference(document_id=document_id, file_sha256=metadata["sha256"], section="身份校验", block_id="身份校验")
            blocks = {block["block_id"]: block for block in data["blocks"]}
            if len(blocks) != len(data["blocks"]):
                raise ValueError("重复定位块")
            for block in blocks.values():
                if block.get("type") == "paragraph" and not isinstance(block.get("text"), str):
                    raise ValueError("段落结构无效")
                if block.get("type") == "table":
                    if not isinstance(block.get("rows"), list) or any(
                        not isinstance(row, list) or any(not isinstance(cell, str) for cell in row)
                        for row in block["rows"]
                    ):
                        raise ValueError("表格结构无效")
            documents[document_id] = (metadata["sha256"], blocks)
        except (OSError, ValueError, TypeError, KeyError):
            problems[document_id] = "包内来源缺失、不可读或清单身份不一致"
    return documents, problems


def pending_reviews(root: Path) -> list[dict]:
    """Return six unresolved reviews with verified, literal packaged citations.

    Original-file identity is handled by the evidence reader. These references
    retain the packaged hashes even when another local DOCX has matching text.
    Missing blocks/text are reported explicitly and produce no fake citations.
    """
    documents, problems = _package_versions(Path(root))
    reviews = []
    for issue_id, title, scope, question, record_ids, locations in _PENDING_SPECS:
        refs, missing = [], []
        for document_id, block_id, section, excerpt in locations:
            if document_id not in documents:
                missing.append({"document_id": document_id, "block_id": block_id,
                                "reason": problems.get(document_id, "来源不可用")})
                continue
            file_hash, blocks = documents[document_id]
            block = blocks.get(block_id)
            text = ""
            if isinstance(block, dict):
                text = block.get("text", "") or "\n".join(
                    "\t".join(row) for row in block.get("rows", [])
                )
            if excerpt not in text:
                missing.append({"document_id": document_id, "block_id": block_id,
                                "reason": "未找到指定原句，不能确认该引用"})
                continue
            refs.append(SourceReference(document_id=document_id, file_sha256=file_hash,
                                        section=section, block_id=block_id, excerpt=excerpt).model_dump(mode="json", exclude_none=True))
        reviews.append({
            "review_id": issue_id, "title": title, "scope": scope, "status": "pending",
            "question": question, "record_ids": list(record_ids), "source_refs": refs,
            "evidence_status": "available" if not missing else "missing_or_partial",
            "missing_evidence": missing, "reference_basis": "packaged_extracted_text",
            "resolution": None,
        })
    return reviews
