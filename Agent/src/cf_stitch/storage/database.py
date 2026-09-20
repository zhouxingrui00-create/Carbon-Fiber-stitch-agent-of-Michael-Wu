"""Versioned local storage with immutable source claims and draft tasks."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Iterator

from cf_stitch.knowledge.seeds import load_seed_bundle
from cf_stitch.knowledge.retrieval import normalize_search_text, search_terms

if TYPE_CHECKING:
    from cf_stitch.domain.schemas import ResearchTask


SCHEMA_VERSION = 3


class DatabaseVersionError(RuntimeError):
    """Opening an unsupported or inconsistent database would lose provenance."""


class SeedConflictError(ValueError):
    """A changed package requires an explicit future migration, never an upsert."""


class EvidenceConflictError(ValueError):
    """The same source version must not acquire different evidence silently."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


_MIGRATION_1 = (
    """CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)""",
    """CREATE TABLE seed_bundles (
        bundle_id TEXT PRIMARY KEY,
        schema_version TEXT NOT NULL,
        identity_json TEXT NOT NULL CHECK(json_valid(identity_json)),
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        raw_yaml TEXT NOT NULL,
        raw_manifest_json TEXT NOT NULL CHECK(json_valid(raw_manifest_json)),
        imported_at TEXT NOT NULL)""",
    """CREATE TABLE sources (
        document_id TEXT PRIMARY KEY,
        sha256 TEXT NOT NULL CHECK(length(sha256)=64),
        bundle_id TEXT NOT NULL REFERENCES seed_bundles(bundle_id),
        verification_scope TEXT NOT NULL CHECK(verification_scope='packaged_manifest_and_extracted_only'),
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        UNIQUE(document_id, sha256))""",
    """CREATE TABLE parameter_claims (
        record_id TEXT PRIMARY KEY,
        bundle_id TEXT NOT NULL REFERENCES seed_bundles(bundle_id),
        source_kind TEXT NOT NULL CHECK(source_kind='document_claim'),
        verification_status TEXT NOT NULL CHECK(verification_status='source_only'),
        enforcement TEXT NOT NULL CHECK(enforcement='advisory'),
        ordinal INTEGER NOT NULL CHECK(ordinal>=0),
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)))""",
    """CREATE TABLE parameter_source_refs (
        record_id TEXT NOT NULL REFERENCES parameter_claims(record_id),
        ordinal INTEGER NOT NULL CHECK(ordinal>=0),
        document_id TEXT NOT NULL,
        file_sha256 TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        PRIMARY KEY(record_id, ordinal),
        FOREIGN KEY(document_id,file_sha256) REFERENCES sources(document_id,sha256))""",
    """CREATE TABLE tasks (
        task_id TEXT PRIMARY KEY,
        namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
        status TEXT NOT NULL CHECK(status='draft'),
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        UNIQUE(task_id,namespace))""",
    """CREATE TABLE process_runs (
        run_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        UNIQUE(run_id,namespace),
        FOREIGN KEY(task_id,namespace) REFERENCES tasks(task_id,namespace))""",
    """CREATE TABLE measurements (
        measurement_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        FOREIGN KEY(run_id,namespace) REFERENCES process_runs(run_id,namespace))""",
    """CREATE TABLE model_runs (
        model_id TEXT PRIMARY KEY,
        namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
        training_state TEXT NOT NULL CHECK(training_state IN ('not_ready','trained')),
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)))""",
    """CREATE TABLE confirmed_equipment (
        equipment_id TEXT PRIMARY KEY,
        namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)))""",
    "CREATE INDEX tasks_namespace_created ON tasks(namespace,created_at)",
)


_MIGRATION_2 = (
    """CREATE TABLE document_versions (
        version_id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        file_sha256 TEXT NOT NULL CHECK(length(file_sha256)=64),
        source_kind TEXT NOT NULL CHECK(source_kind IN ('original_docx','extracted_json')),
        extraction_sha256 TEXT,
        identity_status TEXT NOT NULL CHECK(identity_status IN ('matched','mismatch','declared_only')),
        imported_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)))""",
    """CREATE TABLE evidence_blocks (
        version_id TEXT NOT NULL REFERENCES document_versions(version_id),
        block_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal>=0),
        kind TEXT NOT NULL CHECK(kind IN ('paragraph','table','table_row')),
        original_text TEXT NOT NULL,
        search_text TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        PRIMARY KEY(version_id,block_id),
        UNIQUE(version_id,ordinal))""",
    """CREATE TABLE parameter_confirmations (
        confirmation_id TEXT PRIMARY KEY,
        source_record_id TEXT NOT NULL REFERENCES parameter_claims(record_id),
        namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
        category TEXT NOT NULL CHECK(category='confirmed_equipment_limit'),
        reviewer TEXT NOT NULL CHECK(length(trim(reviewer))>0),
        reason TEXT NOT NULL CHECK(length(trim(reason))>0),
        confirmed_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)))""",
    "CREATE INDEX evidence_source_identity ON document_versions(document_id,file_sha256,source_kind)",
    "CREATE INDEX confirmation_source ON parameter_confirmations(source_record_id,namespace)",
)


class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Apply ordered migrations atomically without rewriting existing records."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, SCHEMA_VERSION):
                raise DatabaseVersionError(f"不支持数据库版本 {version}；当前程序支持 {SCHEMA_VERSION}。")
            if version:
                try:
                    migration = connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
                except sqlite3.DatabaseError as exc:
                    raise DatabaseVersionError("数据库迁移记录缺失，不能继续使用。") from exc
                if [row[0] for row in migration] != list(range(1, version + 1)):
                    raise DatabaseVersionError("数据库版本与迁移记录不一致。")
            if version == SCHEMA_VERSION:
                return
            if not version:
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                if tables:
                    raise DatabaseVersionError("检测到未版本化的已有数据库；不会覆盖或自动升级。")
            from cf_stitch.storage.experiments import MIGRATION_3, IMMUTABLE_TABLES

            migrations = (
                (1, "foundation_sources_and_draft_tasks", _MIGRATION_1,
                 ("seed_bundles", "sources", "parameter_claims", "parameter_source_refs")),
                (2, "versioned_evidence_and_separate_confirmations", _MIGRATION_2,
                 ("document_versions", "evidence_blocks", "parameter_confirmations")),
                (3, "traceable_experiment_records", MIGRATION_3, IMMUTABLE_TABLES),
            )
            for target, name, statements, immutable_tables in migrations:
                if target <= version:
                    continue
                for statement in statements:
                    connection.execute(statement)
                for table in immutable_tables:
                    for operation in ("UPDATE", "DELETE"):
                        connection.execute(
                            f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                            "BEGIN SELECT RAISE(ABORT,'source declarations are read-only'); END"
                        )
                connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (
                    target, name, datetime.now(timezone.utc).isoformat(),
                ))
                connection.execute(f"PRAGMA user_version={target}")

    def schema_version(self) -> int:
        with self._connection() as connection:
            return connection.execute("PRAGMA user_version").fetchone()[0]

    def import_seed_bundle(self, project_root: Path) -> int:
        """Import declarations atomically; return newly inserted record count."""
        bundle = load_seed_bundle(project_root)
        identity = _json(bundle.identity)
        bundle_id = "domain_parameters"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT identity_json, payload_json FROM seed_bundles WHERE bundle_id=?", (bundle_id,)).fetchone()
            if existing:
                if existing["identity_json"] != identity or existing["payload_json"] != _json(bundle.payload):
                    raise SeedConflictError("种子版本或文件哈希已变化；保留已导入声明，需要明确迁移后再导入。")
                count = connection.execute("SELECT COUNT(*) FROM parameter_claims WHERE bundle_id=?", (bundle_id,)).fetchone()[0]
                if count != len(bundle.payload["records"]):
                    raise SeedConflictError("已导入种子的声明数不一致，数据库需要检查。")
                return 0
            connection.execute("INSERT INTO seed_bundles VALUES (?, ?, ?, ?, ?, ?, ?)", (
                bundle_id, bundle.payload["schema_version"], identity, _json(bundle.payload),
                bundle.raw_yaml, bundle.raw_manifest, datetime.now(timezone.utc).isoformat(),
            ))
            for source in bundle.sources:
                connection.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?)", (
                    source["document_id"], source["sha256"], bundle_id,
                    bundle.identity["verification_scope"], _json(source),
                ))
            for ordinal, record in enumerate(bundle.payload["records"]):
                connection.execute("INSERT INTO parameter_claims VALUES (?, ?, ?, ?, ?, ?, ?)", (
                    record["record_id"], bundle_id, "document_claim", "source_only", "advisory", ordinal, _json(record),
                ))
                for ref_ordinal, ref in enumerate(record["source_refs"]):
                    connection.execute("INSERT INTO parameter_source_refs VALUES (?, ?, ?, ?, ?)", (
                        record["record_id"], ref_ordinal, ref["document_id"], ref["file_sha256"], _json(ref),
                    ))
            return len(bundle.payload["records"])

    def list_parameters(self) -> list[dict]:
        with self._connection() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT payload_json FROM parameter_claims ORDER BY ordinal")]

    def list_sources(self) -> list[dict]:
        with self._connection() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT payload_json FROM sources ORDER BY document_id")]

    def import_document(self, snapshot: dict) -> str:
        """Store one immutable extraction atomically, separately from seed claims."""
        # JSON round trip rejects nonfinite values and isolates caller mutations.
        payload = json.loads(_json(snapshot))
        if not isinstance(payload, dict):
            raise ValueError("文档快照必须是对象。")
        for key in ("document_id", "filename", "source_path", "source_kind", "identity_status"):
            if not isinstance(payload.get(key), str) or not payload[key].strip():
                raise ValueError(f"文档快照缺少 {key}。")
        for key in ("sha256", "expected_sha256"):
            value = payload.get(key)
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"{key} 必须是小写 SHA-256。")
        source_kind = payload["source_kind"]
        if source_kind == "original_docx":
            expected_status = "matched" if payload["sha256"] == payload["expected_sha256"] else "mismatch"
        elif source_kind == "extracted_json":
            expected_status = "declared_only"
            extracted_hash = payload.get("extraction_sha256")
            if not isinstance(extracted_hash, str) or len(extracted_hash) != 64 or any(c not in "0123456789abcdef" for c in extracted_hash):
                raise ValueError("提取 JSON 必须保存其自身 extraction_sha256。")
        else:
            raise ValueError("仅支持 original_docx 或 extracted_json 证据。")
        if payload["identity_status"] != expected_status:
            raise ValueError("文件身份状态与哈希/来源类型不一致。")
        blocks = payload.pop("blocks", None)
        if not isinstance(blocks, list):
            raise ValueError("文档快照必须包含 blocks 列表。")
        seen = set()
        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get("block_id"), str):
                raise ValueError("证据块必须有 block_id。")
            block_id = block["block_id"]
            if not block_id.startswith(payload["document_id"] + ":") or block_id in seen:
                raise ValueError("证据块 ID 的文档前缀不一致或发生重复。")
            seen.add(block_id)
            if block.get("kind") not in ("paragraph", "table", "table_row") or not isinstance(block.get("text"), str):
                raise ValueError("证据块必须保存合法类型与原文文本。")
        # One file identity can have multiple acquisition locations/conditions.
        # Keep those observations instead of treating a copied file as tampering.
        identity = {key: payload.get(key) for key in (
            "document_id", "sha256", "source_kind", "extraction_sha256",
            "source_path", "filename", "warnings",
        )}
        version_id = sha256(_json(identity).encode("utf-8")).hexdigest()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload_json FROM document_versions WHERE version_id=?", (version_id,)).fetchone()
            if existing:
                previous_blocks = [json.loads(row[0]) for row in connection.execute(
                    "SELECT payload_json FROM evidence_blocks WHERE version_id=? ORDER BY ordinal", (version_id,),
                )]
                if json.loads(existing["payload_json"]) != payload or previous_blocks != blocks:
                    raise EvidenceConflictError("相同来源版本出现不同元数据或证据；保留原记录，不自动覆盖。")
                return version_id
            connection.execute("INSERT INTO document_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                version_id, payload["document_id"], payload["sha256"], source_kind,
                payload.get("extraction_sha256"), payload["identity_status"],
                datetime.now(timezone.utc).isoformat(), _json(payload),
            ))
            for ordinal, block in enumerate(blocks):
                search_text = normalize_search_text(" ".join((block["text"], str(block.get("section") or ""), block["block_id"])))
                connection.execute("INSERT INTO evidence_blocks VALUES (?, ?, ?, ?, ?, ?, ?)", (
                    version_id, block["block_id"], ordinal, block["kind"], block["text"], search_text, _json(block),
                ))
        return version_id

    def list_document_versions(self) -> list[dict]:
        with self._connection() as connection:
            return [dict(json.loads(row["payload_json"]), version_id=row["version_id"], imported_at=row["imported_at"])
                    for row in connection.execute("SELECT * FROM document_versions ORDER BY document_id, imported_at, version_id")]

    @staticmethod
    def _evidence_result(row: sqlite3.Row) -> dict:
        metadata = json.loads(row["document_payload"])
        result = json.loads(row["block_payload"])
        result.update({key: metadata.get(key) for key in (
            "document_id", "sha256", "expected_sha256", "source_kind", "filename", "source_path",
            "identity_status", "extraction_sha256",
            "hash_basis", "images_parsed", "complete_content", "parser_version", "image_count_basis",
        )})
        result["file_sha256"] = metadata["sha256"]
        result["version_id"] = row["version_id"]
        result["source_limitations"] = metadata.get("limitations", [])
        return result

    def get_evidence(self, document_id: str, file_sha256: str, block_id: str, source_kind: str | None = None,
                     *, version_id: str | None = None) -> dict | None:
        """Resolve exact hash and block; a mismatching hash never falls back."""
        clauses = ["v.document_id=?", "v.file_sha256=?", "b.block_id=?"]
        arguments: list = [document_id, file_sha256, block_id]
        if source_kind is not None:
            clauses.append("v.source_kind=?")
            arguments.append(source_kind)
        if version_id is not None:
            clauses.append("v.version_id=?")
            arguments.append(version_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT v.version_id,v.payload_json AS document_payload,b.payload_json AS block_payload "
                "FROM document_versions v JOIN evidence_blocks b ON b.version_id=v.version_id WHERE "
                + " AND ".join(clauses)
                + " ORDER BY CASE v.source_kind WHEN 'original_docx' THEN 0 ELSE 1 END,v.imported_at DESC,v.version_id LIMIT 1",
                arguments,
            ).fetchone()
            return self._evidence_result(row) if row else None

    def search_evidence(self, query: str, document_id: str | None = None, limit: int = 50,
                        source_kind: str | None = None, version_id: str | None = None,
                        version_ids: list[str] | None = None) -> list[dict]:
        """Literal AND search; default is latest preferred original per document."""
        terms = search_terms(query)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("检索条数必须为 1–200 的整数。")
        if version_id is not None and version_ids is not None:
            raise ValueError("不能同时指定 version_id 和 version_ids。")
        if version_ids is not None and (not isinstance(version_ids, list) or not all(isinstance(value, str) for value in version_ids)):
            raise ValueError("version_ids 必须是字符串列表。")
        if not terms or version_ids == []:
            return []
        clauses = ["instr(b.search_text,?)>0" for _ in terms]
        arguments: list = list(terms)
        for field, value in (("document_id", document_id), ("source_kind", source_kind), ("version_id", version_id)):
            if value is not None:
                clauses.append(f"v.{field}=?")
                arguments.append(value)
        if version_ids is not None:
            clauses.append("v.version_id IN (" + ",".join("?" for _ in version_ids) + ")")
            arguments.extend(version_ids)
        elif version_id is None and source_kind is None:
            clauses.append("""v.version_id IN (SELECT version_id FROM (
                SELECT version_id,ROW_NUMBER() OVER (PARTITION BY document_id
                ORDER BY CASE source_kind WHEN 'original_docx' THEN 0 ELSE 1 END,
                imported_at DESC,version_id) AS preference FROM document_versions
                ) WHERE preference=1)""")
        arguments.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT v.version_id,v.payload_json AS document_payload,b.payload_json AS block_payload "
                "FROM document_versions v JOIN evidence_blocks b ON b.version_id=v.version_id WHERE "
                + " AND ".join(clauses) + " ORDER BY v.document_id,v.imported_at DESC,b.ordinal LIMIT ?", arguments,
            ).fetchall()
            return [self._evidence_result(row) for row in rows]

    def save_parameter_confirmation(self, confirmation: dict) -> str:
        """Append explicit human approval without updating source or equipment."""
        from cf_stitch.knowledge.parameters import ManualParameterConfirmation

        validated = ManualParameterConfirmation.model_validate(confirmation)
        payload = validated.model_dump(mode="json")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute("SELECT payload_json FROM parameter_claims WHERE record_id=?", (payload["source_record_id"],)).fetchone()
            if source is None:
                raise ValueError("人工确认必须关联已有来源声明。")
            claim = json.loads(source[0])
            if payload["field"] != claim["field"] or payload["unit"] != claim["unit"]:
                raise ValueError("人工确认字段及单位必须与所引用声明一致；禁止隐式语义转换。")
            def reference_identity(ref: dict) -> tuple[str, str, str]:
                return ref["document_id"], ref["file_sha256"], ref["block_id"]

            source_anchors = {reference_identity(ref) for ref in claim["source_refs"]}
            approval_anchors = {reference_identity(ref) for ref in payload["source_refs"]}
            if not source_anchors.issubset(approval_anchors):
                raise ValueError("人工确认必须保留该参数声明的原始来源引用，不能改绑其他参数证据。")
            for ref in payload["source_refs"]:
                found = connection.execute(
                    "SELECT 1 FROM document_versions v JOIN evidence_blocks b ON b.version_id=v.version_id "
                    "WHERE v.document_id=? AND v.file_sha256=? AND b.block_id=? LIMIT 1",
                    (ref["document_id"], ref["file_sha256"], ref["block_id"]),
                ).fetchone()
                if found is None:
                    raise ValueError("人工确认引用无法定位到已索引原文，不能保存。")
            connection.execute("INSERT INTO parameter_confirmations VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                payload["confirmation_id"], payload["source_record_id"], self._namespace(payload["namespace"]),
                payload["category"], payload["reviewer"], payload["reason"], payload["confirmed_at"], _json(payload),
            ))
        return payload["confirmation_id"]

    def list_parameter_confirmations(self, namespace: str = "real") -> list[dict]:
        namespace = self._namespace(namespace)
        with self._connection() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT payload_json FROM parameter_confirmations WHERE namespace=? ORDER BY confirmed_at,confirmation_id", (namespace,),
            )]

    @staticmethod
    def _namespace(namespace: str) -> str:
        if namespace not in ("real", "demo"):
            raise ValueError("数据空间只能为 real 或 demo。")
        return namespace

    def save_task(self, task: ResearchTask) -> str:
        """Persist a validated draft, never an experiment or confirmed setting."""
        from cf_stitch.domain.schemas import ResearchTask

        if not isinstance(task, ResearchTask):
            raise TypeError("任务必须先通过 ResearchTask 校验。")
        # Validate serialized values again, including objects changed in memory.
        validated = ResearchTask.model_validate(task.model_dump(mode="python"))
        payload = validated.model_dump(mode="json")
        with self._connection() as connection:
            connection.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)", (
                payload["task_id"], self._namespace(payload["namespace"]), payload["status"],
                payload["created_at"], _json(payload),
            ))
        return payload["task_id"]

    def list_tasks(self, namespace: str = "real") -> list[dict]:
        namespace = self._namespace(namespace)
        with self._connection() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT payload_json FROM tasks WHERE namespace=? ORDER BY created_at DESC, task_id", (namespace,),
            )]

    def counts(self, namespace: str = "real") -> dict[str, int]:
        namespace = self._namespace(namespace)
        with self._connection() as connection:
            result = {}
            for label, table in (("tasks", "tasks"), ("confirmed_equipment", "confirmed_equipment")):
                result[label] = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE namespace=?", (namespace,)).fetchone()[0]
            provenance = "demo" if namespace == "demo" else "experimental_measured"
            # The legacy v1 tables remain available, but an untyped historical
            # row or a simulation is not evidence of a real measured label.
            result["measurements"] = connection.execute(
                "SELECT COUNT(*) FROM measurements WHERE namespace=? AND json_extract(payload_json,'$.provenance')=? AND json_type(payload_json,'$.value') IN ('integer','real')",
                (namespace, provenance),
            ).fetchone()[0]
            result["experiments"] = connection.execute(
                "SELECT COUNT(DISTINCT run_id) FROM measurements WHERE namespace=? AND json_extract(payload_json,'$.provenance')=? AND json_type(payload_json,'$.value') IN ('integer','real')",
                (namespace, provenance),
            ).fetchone()[0]
            result["trained_models"] = connection.execute(
                "SELECT COUNT(*) FROM model_runs WHERE namespace=? AND training_state='trained'", (namespace,),
            ).fetchone()[0]
            if connection.execute("PRAGMA user_version").fetchone()[0] >= 3:
                # Historical rows remain untouched. Only explicitly measured
                # new outcomes with non-null values contribute actual labels.
                result["measurements"] += connection.execute(
                    "SELECT COUNT(*) FROM experiment_measurements WHERE namespace=? AND provenance=? AND value IS NOT NULL",
                    (namespace, provenance),
                ).fetchone()[0]
                result["experiments"] += connection.execute(
                    "SELECT COUNT(DISTINCT run_id) FROM experiment_measurements WHERE namespace=? AND provenance=? AND value IS NOT NULL",
                    (namespace, provenance),
                ).fetchone()[0]
            return result
