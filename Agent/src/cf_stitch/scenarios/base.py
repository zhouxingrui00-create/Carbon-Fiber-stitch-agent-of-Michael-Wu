"""Read-only scenario cards. A source claim is never an equipment approval."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cf_stitch.knowledge.documents import DocumentReadError, load_extracted_document
from cf_stitch.knowledge.seeds import SeedValidationError, load_seed_bundle


class ScenarioEvidenceError(ValueError):
    """A card cannot be built from missing or inconsistent source evidence."""


@dataclass(frozen=True)
class ScenarioConfig:
    key: str
    label: str
    claims: list[dict]
    context_schema: list[dict]
    pending: list[str]
    notes: list[str]
    source_refs: list[dict]
    arrangements: list[dict] = field(default_factory=list)
    parameter_bindings: list[dict] = field(default_factory=list)
    reachability_status: str = "UNKNOWN"
    collision_status: str = "UNKNOWN"
    candidate_approval: bool = False

    @property
    def context_fields(self) -> list[str]:
        return [item["field"] for item in self.context_schema]

    def to_dict(self) -> dict:
        result = asdict(self)
        result["context_fields"] = self.context_fields
        return result


class ScenarioSources:
    """Use the immutable package version, without borrowing a new DOCX hash.

    The extracted JSON proves availability of the quoted text only. Its original
    DOCX hash is a declaration; images and current-file identity are not verified.
    Every load is fresh, so changing a returned card never changes later cards.
    """

    def __init__(self, root: Path):
        try:
            self.bundle = load_seed_bundle(Path(root))
            self.documents = {
                item["document_id"]: load_extracted_document(Path(root), item["document_id"])
                for item in self.bundle.sources
            }
            for document_id, document in self.documents.items():
                if document.extraction_sha256 != self.bundle.identity["extracted_sha256"][document_id]:
                    raise ScenarioEvidenceError("场景读取期间提取文件发生变化，请重试。")
        except (OSError, ValueError, KeyError, TypeError, DocumentReadError, SeedValidationError) as exc:
            raise ScenarioEvidenceError(f"场景来源不可用，不能提供无来源的默认配置：{exc}") from exc

    def evidence(self, document_id: str, block_id: str, *, required_text: str | None = None) -> dict:
        document = self.documents.get(document_id)
        block = next((item for item in document.blocks if item.block_id == block_id), None) if document else None
        if block is None or (required_text is not None and required_text not in block.text):
            raise ScenarioEvidenceError(f"场景声明缺少可定位原文：{document_id}/{block_id}")
        return {
            **block.to_dict(),
            "document_id": document_id,
            "file_sha256": document.sha256,
            "extraction_sha256": document.extraction_sha256,
            "source_kind": document.source_kind,
            "hash_basis": document.hash_basis,
            "identity_status": document.identity_status,
            "images_parsed": False,
        }

    def seed_claims(self, ids: tuple[str, ...], scope: str | None = None) -> list[dict]:
        records = {item["record_id"]: item for item in self.bundle.payload["records"]}
        claims = []
        for record_id in ids:
            if record_id not in records or (scope is not None and records[record_id]["scope"] != scope):
                raise ScenarioEvidenceError(f"场景参数声明缺失或适用范围改变：{record_id}")
            claim = deepcopy(records[record_id])
            claim["evidence_blocks"] = []
            for ref in claim["source_refs"]:
                evidence = self.evidence(ref["document_id"], ref["block_id"])
                if evidence["file_sha256"] != ref["file_sha256"]:
                    raise ScenarioEvidenceError(f"场景参数来源哈希不一致：{record_id}")
                claim["evidence_blocks"].append(evidence)
            claims.append(claim)
        return claims

    def declaration(
        self, record_id: str, field_name: str, label: str, unit: str, scope: str,
        block_id: str, required_text: str, *, section: str, **values,
    ) -> dict:
        evidence = self.evidence("D1", block_id, required_text=required_text)
        ref = {
            "document_id": "D1", "file_sha256": evidence["file_sha256"],
            "block_id": block_id, "section": section, "excerpt": required_text,
        }
        return {
            "record_id": record_id, "field": field_name, "label_zh": label, "unit": unit,
            "scope": scope, "role": "equipment_claim", "evidence_kind": "design_specification",
            "verification_status": "source_only", "enforcement": "advisory",
            "source_refs": [ref], "evidence_blocks": [evidence], **values,
        }


def context(field_name: str, label: str, unit: str | None = None, *, note: str = "") -> dict:
    """Field definitions are software schema, without invented numeric defaults."""
    return {
        "field": field_name, "label_zh": label, "unit": unit, "default": None,
        "missing_reason": "用户尚未提供；不能从设备设计声明自动填入。",
        "definition_basis": "PROJECT_SPEC.md 场景记录字段（非来源数值）", "note": note,
    }


def build_card(key, label, claims, context_schema, pending, notes, *, arrangements=None, parameter_bindings=None):
    refs = []
    for claim in claims:
        for ref in claim["source_refs"]:
            if ref not in refs:
                refs.append(deepcopy(ref))
    for arrangement in arrangements or []:
        for ref in arrangement.get("source_refs", []):
            if ref not in refs:
                refs.append(deepcopy(ref))
    return ScenarioConfig(
        key=key, label=label, claims=claims, context_schema=context_schema,
        pending=pending, notes=[
            "来源窗口只供研究审查；所有设计规格保持 source_only/advisory，不是确认设备硬限。",
            "来源引用固定指向包内提取版本；原件哈希为声明值，图片和工程图未解析。",
            "缺少已验证 CAD、机构、夹具、机型/载荷及 TCP 标定，不能验证可达性或避碰。",
            *notes,
        ], source_refs=refs, arrangements=arrangements or [],
        parameter_bindings=parameter_bindings or [],
    )
