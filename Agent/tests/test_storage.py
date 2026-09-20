"""Persistence tests use temporary databases, never the user's live records."""

from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from cf_stitch.knowledge.seeds import (
    SeedValidationError, load_experiment_template, load_parameter_seed,
)
from cf_stitch.storage.database import Database, DatabaseVersionError, SeedConflictError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database(tmp_path):
    result = Database(tmp_path / "private" / "foundation.sqlite3")
    result.initialize()
    return result


@pytest.fixture
def copied_package(tmp_path):
    root = tmp_path / "source-package"
    shutil.copytree(PROJECT_ROOT / "spec", root / "spec")
    shutil.copytree(PROJECT_ROOT / "sources", root / "sources")
    return root


def test_empty_database_migration_is_idempotent(database):
    database.initialize()
    assert database.schema_version() == 3
    assert database.list_parameters() == []
    assert database.list_sources() == []
    assert database.list_tasks() == []
    assert database.counts() == {
        "tasks": 0, "experiments": 0, "measurements": 0,
        "trained_models": 0, "confirmed_equipment": 0,
    }
    assert database.counts("demo") == database.counts("real")
    with database._connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3


def test_seed_import_is_idempotent_and_preserves_every_claim(database):
    original = load_parameter_seed(PROJECT_ROOT)
    assert database.import_seed_bundle(PROJECT_ROOT) == 33
    assert database.import_seed_bundle(PROJECT_ROOT) == 0
    assert database.list_parameters() == original["records"]
    manifest_raw = (PROJECT_ROOT / "sources" / "manifest.json").read_bytes()
    seed_raw = (PROJECT_ROOT / "spec" / "domain_parameters.yaml").read_bytes()
    assert database.list_sources() == json.loads(manifest_raw)
    assert all(value == 0 for value in database.counts().values())
    with database._connection() as connection:
        row = connection.execute("SELECT * FROM seed_bundles").fetchone()
        identity = json.loads(row["identity_json"])
        assert identity["parameter_sha256"] == sha256(seed_raw).hexdigest()
        assert identity["manifest_sha256"] == sha256(manifest_raw).hexdigest()
        assert identity["verification_scope"] == "packaged_manifest_and_extracted_only"
        assert row["raw_yaml"] == seed_raw.decode("utf-8-sig")
        assert json.loads(row["payload_json"]) == original
        assert connection.execute("SELECT DISTINCT source_kind,verification_status,enforcement FROM parameter_claims").fetchall()[0][:] == (
            "document_claim", "source_only", "advisory",
        )
    # Measurement recommendations and design targets remain source statements.
    claims = {item["record_id"]: item for item in database.list_parameters()}
    assert claims["D2-PIN"]["min"] is None
    assert claims["D2-PIN"]["role"] == "measurement"
    assert claims["D1-V-STRENGTH"]["evidence_kind"] == "design_target"
    assert claims["D1-DUAL-H5"]["max"] == 5
    assert claims["D1-DUAL-H6"]["max"] == 6


@pytest.mark.parametrize("table,key", [
    ("seed_bundles", "bundle_id"), ("sources", "document_id"),
    ("parameter_claims", "record_id"), ("parameter_source_refs", "record_id"),
])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_seed_claims_cannot_be_modified_or_deleted(database, table, key, operation):
    database.import_seed_bundle(PROJECT_ROOT)
    query = f"UPDATE {table} SET {key}={key}" if operation == "UPDATE" else f"DELETE FROM {table}"
    with pytest.raises(sqlite3.IntegrityError, match="read-only"):
        with database._connection() as connection:
            connection.execute(query)
    assert len(database.list_parameters()) == 33
    assert len(database.list_sources()) == 2


def test_seed_hash_changes_are_rejected_without_overwriting(database, copied_package):
    database.import_seed_bundle(copied_package)
    path = copied_package / "spec" / "domain_parameters.yaml"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(SeedConflictError, match="哈希"):
        database.import_seed_bundle(copied_package)
    assert database.list_parameters() == load_parameter_seed(PROJECT_ROOT)["records"]


def test_import_rolls_back_the_entire_bundle_on_storage_failure(database):
    with database._connection() as connection:
        connection.execute("""CREATE TRIGGER fail_second_source BEFORE INSERT ON sources
            WHEN NEW.document_id='D2' BEGIN SELECT RAISE(ABORT,'test insertion failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="test insertion failure"):
        database.import_seed_bundle(PROJECT_ROOT)
    assert database.list_sources() == []
    assert database.list_parameters() == []
    with database._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM seed_bundles").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM parameter_source_refs").fetchone()[0] == 0


@pytest.mark.parametrize("old,new", [
    ("enforcement: advisory", "enforcement: hard_limit"),
    ("verification_status: source_only", "verification_status: measured"),
    ("evidence_kind: initial_trial_window", "evidence_kind: experimental_measured"),
    ("evidence_kind: initial_trial_window", "evidence_kind: confirmed_equipment_limit"),
    ("default_promote_to_hard_limits: false", "default_promote_to_hard_limits: true"),
    ("schema_version: '1.0'", "schema_version: '9.0'"),
    ("block_id: D2:t002", "block_id: D2:missing"),
])
def test_invalid_source_packages_leave_database_empty(database, copied_package, old, new):
    path = copied_package / "spec" / "domain_parameters.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
    with pytest.raises(SeedValidationError):
        database.import_seed_bundle(copied_package)
    assert database.list_parameters() == []
    assert database.list_sources() == []


def test_yaml_duplicate_keys_are_rejected(copied_package):
    path = copied_package / "spec" / "domain_parameters.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\nschema_version: '1.0'\n", encoding="utf-8")
    with pytest.raises(SeedValidationError, match="重复键"):
        load_parameter_seed(copied_package)


def test_templates_are_read_only_plans_with_no_results(database):
    template = load_experiment_template(PROJECT_ROOT)
    assert len(template["groups"]) == 7
    assert all(group["measurements"] == [] for group in template["groups"])
    assert all(group["status"] == "planned" for group in template["groups"])
    assert template["groups"][0]["pitch_mm"] is None
    assert template["groups"][0]["row_spacing_mm"] is None
    assert database.counts()["experiments"] == 0
    assert database.counts()["measurements"] == 0


def test_task_persistence_and_namespace_isolation(database):
    from cf_stitch.domain.schemas import MechanismSelection, ResearchTask

    real = ResearchTask(title="实际任务；参数待补充", mechanism=MechanismSelection(kind="unstitched"), namespace="real")
    demo = ResearchTask(title="软件测试演示任务", mechanism=MechanismSelection(kind="unstitched"), namespace="demo")
    assert database.save_task(real) == real.task_id
    assert database.save_task(demo) == demo.task_id
    assert database.list_tasks() == [real.model_dump(mode="json")]
    assert database.list_tasks("demo") == [demo.model_dump(mode="json")]
    reopened = Database(database.db_path)
    reopened.initialize()
    assert reopened.list_tasks() == [real.model_dump(mode="json")]
    assert reopened.counts()["tasks"] == 1
    assert reopened.counts("demo")["tasks"] == 1
    assert reopened.counts()["experiments"] == 0
    assert reopened.counts()["measurements"] == 0
    assert reopened.counts()["trained_models"] == 0
    assert reopened.counts()["confirmed_equipment"] == 0
    with pytest.raises(TypeError):
        database.save_task({"title": "绕过校验"})
    with pytest.raises(ValueError):
        database.list_tasks("real' OR 1=1 --")


def test_foreign_keys_isolate_real_and_demo_runs(database):
    from cf_stitch.domain.schemas import MechanismSelection, ResearchTask

    real = ResearchTask(title="真实空间草案", mechanism=MechanismSelection(kind="unstitched"), namespace="real")
    database.save_task(real)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with database._connection() as connection:
            connection.execute("INSERT INTO process_runs VALUES (?, ?, ?, ?)", ("demo-run", real.task_id, "demo", "{}"))
    assert database.counts()["experiments"] == 0
    assert database.counts("demo")["experiments"] == 0


def test_database_rejects_invalid_json(database):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
        with database._connection() as connection:
            connection.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)", ("bad", "real", "draft", "now", "not-json"))
    assert database.list_tasks() == []


def test_unknown_migration_version_is_rejected_without_change(database):
    with database._connection() as connection:
        connection.execute("PRAGMA user_version=42")
    with pytest.raises(DatabaseVersionError, match="42"):
        database.initialize()
    assert database.schema_version() == 42


def test_unversioned_existing_database_is_not_overwritten(tmp_path):
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE original_data(value TEXT)")
        connection.execute("INSERT INTO original_data VALUES ('preserved')")
    with pytest.raises(DatabaseVersionError, match="未版本化"):
        Database(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM original_data").fetchone()[0] == "preserved"
