"""Append-only experiment entities and transactional, versioned imports."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3
from typing import TYPE_CHECKING

from cf_stitch.experiments.models import ObservationRow, PROVENANCES, validate_rows

if TYPE_CHECKING:
    from cf_stitch.storage.database import Database

NORMALIZATION_VERSION = "observation-v1"
PARSER_VERSION = "csv-xlsx-v1"


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


ENTITY_FIELDS = {
    "sources": ("source_id", ("source_citation",)),
    "materials": ("material_id", ("material_system", "material_batch", "roll_id", "layup_sequence", "forming_conditions", "post_treatment")),
    "parent_preforms": ("parent_preform_id", ("source_id", "material_id", "compacted_thickness_mm", "thickness_condition")),
    "equipment": ("equipment_id", ("equipment_name",)),
    "runs": ("run_id", ("source_id", "material_id", "parent_preform_id", "plan_id", "group_label", "equipment_id", "stitch_mechanism", "raw_mechanism_name", "path_pattern", "motion_platform", *tuple(k for k in ObservationRow.model_fields if "_setting_" in k))),
    "specimens": ("specimen_id", ("source_id", "material_id", "parent_preform_id", "run_id", "run_segment", "specimen_origin", "specimen_geometry")),
}

MIGRATION_3 = tuple(
    f"""CREATE TABLE experiment_{name} (
        namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
        provenance TEXT NOT NULL CHECK(provenance IN {str(PROVENANCES)}),
        {identity} TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        PRIMARY KEY(namespace,provenance,{identity}),
        CHECK((namespace='demo')=(provenance='demo')))"""
    for name, (identity, _) in ENTITY_FIELDS.items()
) + (
    """CREATE TABLE experiment_raw_files (
        sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
        byte_size INTEGER NOT NULL CHECK(byte_size>=0), content BLOB NOT NULL,
        CHECK(length(content)=byte_size))""",
    """CREATE TABLE experiment_import_batches (
        import_id TEXT PRIMARY KEY, namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
        raw_file_sha256 TEXT NOT NULL REFERENCES experiment_raw_files(sha256),
        created_at TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)))""",
    """CREATE TABLE experiment_plans (
        plan_id TEXT NOT NULL, namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
        created_at TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        PRIMARY KEY(namespace,plan_id))""",
    """CREATE TABLE experiment_measurements (
        namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
        provenance TEXT NOT NULL CHECK(provenance IN ('experimental_measured','literature_measured','simulation','demo','prediction')),
        measurement_id TEXT NOT NULL, source_id TEXT NOT NULL, material_id TEXT NOT NULL,
        parent_preform_id TEXT, run_id TEXT, specimen_id TEXT, value REAL,
        semantic_hash TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        PRIMARY KEY(namespace,provenance,measurement_id),
        UNIQUE(namespace,provenance,semantic_hash),
        FOREIGN KEY(namespace,provenance,source_id) REFERENCES experiment_sources(namespace,provenance,source_id),
        FOREIGN KEY(namespace,provenance,material_id) REFERENCES experiment_materials(namespace,provenance,material_id),
        FOREIGN KEY(namespace,provenance,parent_preform_id) REFERENCES experiment_parent_preforms(namespace,provenance,parent_preform_id),
        FOREIGN KEY(namespace,provenance,run_id) REFERENCES experiment_runs(namespace,provenance,run_id),
        FOREIGN KEY(namespace,provenance,specimen_id) REFERENCES experiment_specimens(namespace,provenance,specimen_id),
        CHECK((namespace='demo')=(provenance='demo')))""",
    """CREATE TABLE experiment_controls (
        namespace TEXT NOT NULL, provenance TEXT NOT NULL, specimen_id TEXT NOT NULL,
        control_specimen_id TEXT NOT NULL,
        PRIMARY KEY(namespace,provenance,specimen_id,control_specimen_id),
        FOREIGN KEY(namespace,provenance,specimen_id) REFERENCES experiment_specimens(namespace,provenance,specimen_id),
        FOREIGN KEY(namespace,provenance,control_specimen_id) REFERENCES experiment_specimens(namespace,provenance,specimen_id),
        CHECK(specimen_id<>control_specimen_id))""",
    """CREATE TABLE experiment_import_rows (
        import_id TEXT NOT NULL REFERENCES experiment_import_batches(import_id), ordinal INTEGER NOT NULL,
        source_row_number INTEGER NOT NULL CHECK(source_row_number>0),
        namespace TEXT NOT NULL, provenance TEXT NOT NULL, measurement_id TEXT NOT NULL,
        PRIMARY KEY(import_id,ordinal),
        FOREIGN KEY(namespace,provenance,measurement_id) REFERENCES experiment_measurements(namespace,provenance,measurement_id))""",
    "CREATE INDEX experiment_measurements_source ON experiment_measurements(namespace,provenance,source_id)",
)
IMMUTABLE_TABLES = tuple(f"experiment_{name}" for name in ENTITY_FIELDS) + (
    "experiment_raw_files", "experiment_import_batches", "experiment_plans", "experiment_measurements", "experiment_controls", "experiment_import_rows",
)


class ExperimentConflictError(ValueError):
    """A whole batch is rejected instead of silently revising prior evidence."""


def _semantic(row: dict) -> str:
    # Identity of one observation is distinct from a chosen measurement ID.
    keys = ("source_id", "specimen_id", "run_id", "metric_name", "unit", "measurement_stage", "test_method", "loading_direction", "observation_window", "aggregation_level")
    if row["provenance"] == "literature_measured":
        # A published location cannot become a new observation merely by
        # relabelling an aggregate as an individual specimen.
        keys = ("source_citation", "literature_record_id", "metric_name", "unit", "measurement_stage", "test_method", "loading_direction")
    elif row["specimen_id"] is None and row["run_id"] is None:
        keys += ("material_id", "group_label", "raw_data_ref")
    return sha256(_json({k: row[k] for k in keys}).encode()).hexdigest()


def _literature_copy_key(row: dict) -> str | None:
    if row["provenance"] != "literature_measured" or row["aggregation_level"] != "aggregate":
        return None
    keys = ("source_citation", "material_system", "material_batch", "layup_sequence", "forming_conditions", "post_treatment", "group_label", "metric_name", "value", "unit", "measurement_stage", "test_method", "loading_direction", "reported_sample_size",
            "stitch_mechanism", "raw_mechanism_name", "path_pattern", "motion_platform", "compacted_thickness_mm", "thickness_condition", "specimen_geometry",
            *tuple(k for k in ObservationRow.model_fields if "_setting_" in k))
    return _json({k: row[k] for k in keys})


class ExperimentStore:
    def __init__(self, db: Database):
        self.db = db

    def _preview(self, connection, rows, namespace):
        result = validate_rows(rows, namespace)
        if result["errors"]:
            return result
        existing = [json.loads(v[0]) for v in connection.execute(
            "SELECT payload_json FROM experiment_measurements WHERE namespace=?", (namespace,))]
        by_id = {(r["provenance"], r["measurement_id"]): r for r in existing}
        by_semantic = {(r["provenance"], _semantic(r)): r for r in existing}
        literature_copies = {_literature_copy_key(r): r for r in existing if _literature_copy_key(r) is not None}
        entities = {}
        for name, (identity, _) in ENTITY_FIELDS.items():
            entities[name] = {(r["provenance"], r[identity]): json.loads(r["payload_json"]) for r in connection.execute(f"SELECT * FROM experiment_{name} WHERE namespace=?", (namespace,))}
        accepted = []
        for number, row in enumerate(result["rows"], 1):
            try:
                identity = (row["provenance"], row["measurement_id"])
                previous = by_id.get(identity)
                if previous:
                    if previous != row:
                        raise ExperimentConflictError("相同 measurement_id 内容改变；禁止覆盖旧记录")
                    result["duplicates"].append({"row": number, "measurement_id": previous["measurement_id"], "reason": "相同记录"})
                    accepted.append(previous)
                    continue
                previous = by_semantic.get((row["provenance"], _semantic(row)))
                if previous:
                    comparable = {k: v for k, v in previous.items() if k != "measurement_id"}
                    if comparable != {k: v for k, v in row.items() if k != "measurement_id"}:
                        raise ExperimentConflictError("同一来源/试样/测量上下文出现不同记录；不能作为独立重复试样")
                    result["duplicates"].append({"row": number, "measurement_id": previous["measurement_id"], "reason": "换 ID 的语义重复"})
                    accepted.append(previous)
                    continue
                copy_key = _literature_copy_key(row)
                if copy_key is not None and copy_key in literature_copies:
                    raise ExperimentConflictError("文献均值内容重复，不得改 ID 复制成独立样本")
                for name, (key, fields) in ENTITY_FIELDS.items():
                    if row[key] is None:
                        continue
                    payload = {k: row[k] for k in ("namespace", "provenance", key, *fields)}
                    entity_key = (row["provenance"], row[key])
                    if entity_key in entities[name] and entities[name][entity_key] != payload:
                        raise ExperimentConflictError(f"同一 {key} 的材料、来源或工艺上下文不一致；不能默默合并")
                    entities[name][entity_key] = payload
                if row["plan_id"]:
                    stored_plan = connection.execute("SELECT payload_json FROM experiment_plans WHERE namespace=? AND plan_id=?", (namespace, row["plan_id"])).fetchone()
                    if not stored_plan:
                        raise ExperimentConflictError("关联 plan_id 不存在于当前数据空间")
                    groups = {g["source_group_label"]: g for g in json.loads(stored_plan[0])["groups"]}
                    group = groups.get(row["group_label"])
                    if group is None:
                        raise ExperimentConflictError("group_label 必须是所关联计划中的原始组名")
                    for setting, planned in (("stitch_mechanism", "stitch_mechanism"), ("pitch_setting_mm", "pitch_mm"), ("row_spacing_setting_mm", "row_spacing_mm")):
                        if row[setting] is not None and row[setting] != group[planned]:
                            raise ExperimentConflictError(f"{setting} 与计划组 {row['group_label']} 不一致；请另存明确的偏离方案")
                    result["warnings"].append({"row": number, "field": "plan_id", "message": "关联待实验计划仅保留来源，不证明设备适配或方案获准；缺失设定不从模板补填"})
                by_id[identity] = row
                by_semantic[(row["provenance"], _semantic(row))] = row
                if copy_key is not None:
                    literature_copies[copy_key] = row
                accepted.append(row)
            except (ExperimentConflictError, ValueError) as exc:
                result["errors"].append({"row": number, "field": "identity", "message": str(exc)})
        # Relationships can refer to later rows of the same atomic batch.
        for number, row in enumerate(result["rows"], 1):
            control = row["control_specimen_id"]
            if not control:
                continue
            target = entities["specimens"].get((row["provenance"], control))
            if target is None or not row["specimen_id"]:
                result["errors"].append({"row": number, "field": "control_specimen_id", "message": "对照必须关联同一来源类别/空间的已有或本批试样"})
                continue
            own_material = entities["materials"].get((row["provenance"], row["material_id"]))
            control_material = entities["materials"].get((row["provenance"], target["material_id"]))
            if own_material is None or control_material is None:
                result["errors"].append({"row": number, "field": "control_specimen_id", "message": "存在实体身份错误，无法核查对照材料关系"})
                continue
            comparison = ("material_system", "layup_sequence", "forming_conditions", "post_treatment")
            different = [k for k in comparison if own_material[k] is not None and control_material[k] is not None and own_material[k] != control_material[k]]
            if different:
                result["errors"].append({"row": number, "field": "control_specimen_id", "message": "对照材料/后处理不匹配: " + ",".join(different)})
            else:
                result["warnings"].append({"row": number, "field": "control_specimen_id", "message": "已记录共享对照关系；关系本身不证明测试方案和其他条件匹配"})
            control_observations = [r for r in by_id.values() if r["provenance"] == row["provenance"] and r["specimen_id"] == control and r["metric_name"] == row["metric_name"]]
            for reference in control_observations:
                different = [k for k in ("test_method", "loading_direction", "unit", "measurement_stage") if row[k] is not None and reference[k] is not None and row[k] != reference[k]]
                if different:
                    result["errors"].append({"row": number, "field": "control_specimen_id", "message": "同指标对照的测试方法/方向/单位/阶段不匹配: " + ",".join(different)})
        result["valid"] = not result["errors"]
        result["rows"] = accepted if result["valid"] else result["rows"]
        return result

    def preview_rows(self, rows: list[ObservationRow | dict], namespace: str = "real") -> dict:
        with self.db._connection() as connection:
            return self._preview(connection, rows, self.db._namespace(namespace))

    def commit_import(self, rows: list[ObservationRow | dict], *, raw_bytes: bytes, filename: str,
                      mapping: dict, sheet_name: str | None = None, namespace: str = "real",
                      row_numbers: list[int] | None = None, encoding: str | None = None) -> dict:
        namespace = self.db._namespace(namespace)
        if not isinstance(raw_bytes, bytes) or not raw_bytes:
            raise ValueError("必须保存非空原始文件 bytes")
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("必须保留文件名")
        if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise ValueError("字段映射必须是字符串字典")
        if row_numbers is None:
            row_numbers = list(range(2, len(rows) + 2))
        if len(row_numbers) != len(rows) or any(type(n) is not int or n <= 0 for n in row_numbers) or len(set(row_numbers)) != len(row_numbers):
            raise ValueError("原始行位置必须与记录一一对应且为不重复正整数")
        raw_hash = sha256(raw_bytes).hexdigest()
        identity = {"namespace": namespace, "raw_file_sha256": raw_hash, "mapping": mapping,
                    "sheet_name": sheet_name, "encoding": encoding, "parser_version": PARSER_VERSION, "normalization_version": NORMALIZATION_VERSION}
        import_id = sha256(_json(identity).encode()).hexdigest()
        with self.db._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            preview = self._preview(connection, rows, namespace)
            if not preview["valid"]:
                raise ExperimentConflictError("；".join(v["message"] for v in preview["errors"]))
            canonical = preview["rows"]
            rows_hash = sha256(_json(canonical).encode()).hexdigest()
            old = connection.execute("SELECT payload_json FROM experiment_import_batches WHERE import_id=?", (import_id,)).fetchone()
            if old:
                if json.loads(old[0])["rows_sha256"] != rows_hash:
                    raise ExperimentConflictError("同一原文件/映射产生不同清洗记录；必须使用明确的新解析版本")
                return {"import_id": import_id, "inserted": 0, "duplicates": len(canonical), "raw_file_sha256": raw_hash, "counts": self.counts(namespace)}
            connection.execute("INSERT OR IGNORE INTO experiment_raw_files VALUES (?,?,?)", (raw_hash, len(raw_bytes), raw_bytes))
            payload = dict(identity, filename=filename, row_count=len(canonical), rows_sha256=rows_hash,
                           row_numbers=row_numbers,
                           measurement_refs=[{"namespace": namespace, "provenance": r["provenance"], "measurement_id": r["measurement_id"], "source_row_number": row_numbers[i]} for i, r in enumerate(canonical)],
                           raw_data_refs_status="row references/hashes are declarations, not verified external files")
            connection.execute("INSERT INTO experiment_import_batches VALUES (?,?,?,?,?)", (import_id, namespace, raw_hash, datetime.now(timezone.utc).isoformat(), _json(payload)))
            for row in canonical:
                for name, (key, fields) in ENTITY_FIELDS.items():
                    if row[key] is None:
                        continue
                    entity = {k: row[k] for k in ("namespace", "provenance", key, *fields)}
                    connection.execute(f"INSERT OR IGNORE INTO experiment_{name} VALUES (?,?,?,?)", (namespace, row["provenance"], row[key], _json(entity)))
            inserted = 0
            for ordinal, row in enumerate(canonical, 1):
                cursor = connection.execute("INSERT OR IGNORE INTO experiment_measurements VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    namespace, row["provenance"], row["measurement_id"], row["source_id"], row["material_id"],
                    row["parent_preform_id"], row["run_id"], row["specimen_id"], row["value"], _semantic(row), _json(row)))
                inserted += cursor.rowcount
                if row["control_specimen_id"]:
                    connection.execute("INSERT OR IGNORE INTO experiment_controls VALUES (?,?,?,?)", (namespace, row["provenance"], row["specimen_id"], row["control_specimen_id"]))
                connection.execute("INSERT INTO experiment_import_rows VALUES (?,?,?,?,?,?)", (import_id, ordinal, row_numbers[ordinal - 1], namespace, row["provenance"], row["measurement_id"]))
        return {"import_id": import_id, "inserted": inserted, "duplicates": len(canonical) - inserted,
                "raw_file_sha256": raw_hash, "counts": self.counts(namespace)}

    def list_measurements(self, namespace: str = "real", provenance: str | None = None) -> list[dict]:
        namespace = self.db._namespace(namespace)
        if provenance is not None and provenance not in PROVENANCES:
            raise ValueError("未知来源类别")
        where, args = "namespace=?", [namespace]
        if provenance:
            where += " AND provenance=?"
            args.append(provenance)
        with self.db._connection() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT payload_json FROM experiment_measurements WHERE " + where + " ORDER BY provenance,measurement_id", args)]

    def counts(self, namespace: str = "real") -> dict:
        namespace = self.db._namespace(namespace)
        with self.db._connection() as connection:
            result = {name: connection.execute(f"SELECT COUNT(*) FROM experiment_{name} WHERE namespace=?", (namespace,)).fetchone()[0]
                      for name in (*ENTITY_FIELDS, "controls", "measurements", "import_batches", "plans")}
            result["raw_files"] = connection.execute("SELECT COUNT(DISTINCT raw_file_sha256) FROM experiment_import_batches WHERE namespace=?", (namespace,)).fetchone()[0]
            for provenance in PROVENANCES:
                result[provenance] = connection.execute("SELECT COUNT(*) FROM experiment_measurements WHERE namespace=? AND provenance=? AND value IS NOT NULL", (namespace, provenance)).fetchone()[0]
            result["labeled_measurements"] = sum(result[p] for p in PROVENANCES)
            return result

    def list_entities(self, entity: str, namespace: str = "real") -> list[dict]:
        namespace = self.db._namespace(namespace)
        aliases = {"source": "sources", "material": "materials", "parent_preform": "parent_preforms", "run": "runs", "specimen": "specimens", "control": "controls", "raw_file": "raw_files", "import_batch": "import_batches", "import_row": "import_rows", "plan": "plans"}
        entity = aliases.get(entity, entity)
        allowed = {*ENTITY_FIELDS, "controls", "raw_files", "import_batches", "import_rows", "plans"}
        if entity not in allowed:
            raise ValueError("未知实体")
        with self.db._connection() as connection:
            if entity == "raw_files":
                return [dict(row) for row in connection.execute("SELECT DISTINCT f.sha256,f.byte_size FROM experiment_raw_files f JOIN experiment_import_batches b ON b.raw_file_sha256=f.sha256 WHERE b.namespace=? ORDER BY f.sha256", (namespace,))]
            if entity == "controls":
                return [dict(row) for row in connection.execute("SELECT * FROM experiment_controls WHERE namespace=? ORDER BY specimen_id,control_specimen_id", (namespace,))]
            if entity == "import_rows":
                return [dict(row) for row in connection.execute("SELECT * FROM experiment_import_rows WHERE namespace=? ORDER BY import_id,ordinal", (namespace,))]
            records = connection.execute(f"SELECT * FROM experiment_{entity} WHERE namespace=?", (namespace,))
            return [dict(json.loads(r["payload_json"]), **({"import_id": r["import_id"], "created_at": r["created_at"]} if entity == "import_batches" else {})) for r in records]

    def dependency_groups(self, namespace: str = "real", provenance: str = "experimental_measured") -> list[dict]:
        rows = self.list_measurements(namespace, provenance)
        parents = list(range(len(rows)))
        def find(index):
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index
        seen = {}
        reasons = {}
        for index, row in enumerate(rows):
            keys = []
            for key in ("parent_preform_id", "run_id", "specimen_id", "control_specimen_id"):
                if row[key]:
                    keys.append(("specimen_id" if key == "control_specimen_id" else key, row[key]))
            if row["roll_id"]:
                # A shared physical roll can be entered in separate source files.
                # Connect conservatively across sources/batches rather than infer
                # independence. Users must disambiguate reused local roll names.
                keys.append(("roll", row["roll_id"]))
            if provenance == "literature_measured":
                keys.append(("literature_source", row["source_citation"]))
            reasons[index] = keys
            for key in keys:
                if key in seen:
                    parents[find(index)] = find(seen[key])
                else:
                    seen[key] = index
        groups = {}
        for index, row in enumerate(rows):
            groups.setdefault(find(index), []).append(index)
        result = []
        for members in groups.values():
            identifiers = sorted(rows[i]["measurement_id"] for i in members)
            group_id = sha256(_json([namespace, provenance, identifiers]).encode()).hexdigest()
            result.append({"leakage_group_id": group_id, "namespace": namespace, "provenance": provenance,
                           "measurement_ids": identifiers, "dependency_keys": sorted({str(k) for i in members for k in reasons[i]}),
                           "training_eligible": False, "basis": "dependency connected component; no training or statistical sufficiency claim"})
        return sorted(result, key=lambda g: g["measurement_ids"])

    def save_plan(self, plan: dict) -> str:
        from cf_stitch.experiments.plans import validate_plan
        payload = validate_plan(plan)
        namespace = self.db._namespace(payload["namespace"])
        with self.db._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload_json FROM experiment_plans WHERE namespace=? AND plan_id=?", (namespace, payload["plan_id"])).fetchone()
            if existing:
                if json.loads(existing[0]) != payload:
                    raise ExperimentConflictError("同一计划 ID 内容改变；保留已有计划，另建版本")
                return payload["plan_id"]
            connection.execute("INSERT INTO experiment_plans VALUES (?,?,?,?)", (payload["plan_id"], namespace, datetime.now(timezone.utc).isoformat(), _json(payload)))
        return payload["plan_id"]

    def list_plans(self, namespace: str = "real") -> list[dict]:
        return self.list_entities("plans", namespace)
