"""Index local evidence without changing source claims or following document text."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cf_stitch.knowledge.documents import DocumentReadError, load_document, load_extracted_document
from cf_stitch.storage.database import Database


@dataclass
class EvidenceIndexReport:
    preferred_versions: list[str] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def index_local_evidence(project_root: Path, db: Database) -> EvidenceIndexReport:
    """Prefer available originals; retain declared-hash JSON for old citations.

    The two allowed document identifiers come from application configuration,
    never from instructions in a document. Each reader failure stays visible.
    A previous database snapshot is not reported as a successful current read.
    """
    report = EvidenceIndexReport()
    for document_id in ("D1", "D2"):
        try:
            snapshot = load_document(project_root, document_id)
        except (DocumentReadError, OSError, ValueError) as exc:
            report.errors.append(f"{document_id} 当前资料无法读取：{exc}")
            continue
        report.preferred_versions.append(db.import_document(snapshot.to_dict()))
        report.documents.append(snapshot.to_dict())
        # The seed still cites the packaged D1 version. Do not replace its hash
        # with the current file's hash even when the paragraph texts match.
        if snapshot.source_kind == "original_docx" and snapshot.sha256 != snapshot.expected_sha256:
            try:
                prior = load_extracted_document(project_root, document_id)
                db.import_document(prior.to_dict())
            except (DocumentReadError, OSError, ValueError) as exc:
                report.errors.append(f"{document_id} 清单版本后备证据无法读取：{exc}")
    return report
