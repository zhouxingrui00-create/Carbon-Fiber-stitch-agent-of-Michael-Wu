"""Transactional checks on disposable databases; outcomes are software fixtures."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from cf_stitch.domain.schemas import MechanismSelection, ResearchTask
from cf_stitch.storage.database import Database, _MIGRATION_1, _MIGRATION_2
from cf_stitch.storage.experiments import ExperimentStore, ExperimentConflictError, IMMUTABLE_TABLES
from test_experiment_models import observation

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "experiments.sqlite3")
    db.initialize()
    return ExperimentStore(db)


def commit(store, rows, data=b"isolated fixture", **kwargs):
    return store.commit_import(rows, raw_bytes=data, filename="fixture.csv", mapping={"metric_name": "metric_name"}, **kwargs)


def measured(**changes):
    return observation(value=12, missing_reason=None, material_batch="batch", roll_id="roll", test_method="fixture method", loading_direction="warp", post_treatment="fixture cured", **changes)


def test_v2_migration_preserves_task_source_and_evidence(tmp_path):
    path = tmp_path / "v2.sqlite3"
    with sqlite3.connect(path) as connection:
        for statements, version in ((_MIGRATION_1, 1), (_MIGRATION_2, 2)):
            for sql in statements:
                connection.execute(sql)
            connection.execute("INSERT INTO schema_migrations VALUES (?, 'fixture prior migration', 'fixture')", (version,))
        connection.execute("PRAGMA user_version=2")
    db = Database(path)
    db.import_seed_bundle(ROOT)
    task = ResearchTask(title="v2 user draft", mechanism=MechanismSelection(kind="unstitched"))
    db.save_task(task)
    from test_evidence_storage import snapshot
    version = db.import_document(snapshot())
    before = db.list_parameters(), db.list_tasks(), db.list_document_versions()
    db.initialize()
    assert db.schema_version() == 3
    assert (db.list_parameters(), db.list_tasks(), db.list_document_versions()) == before
    assert db.get_evidence("D2", snapshot()["sha256"], "D2:t002", version_id=version)
    assert ExperimentStore(db).counts()["measurements"] == 0


def test_preview_never_writes_and_import_is_idempotent(store):
    row = measured()
    assert store.preview_rows([row])["valid"]
    assert store.counts()["measurements"] == 0
    first = commit(store, [row])
    again = commit(store, [row])
    assert first["inserted"] == 1 and again["inserted"] == 0 and again["duplicates"] == 1
    assert first["import_id"] == again["import_id"]
    assert store.counts()["import_batches"] == 1
    assert store.db.counts()["measurements"] == 1


def test_repeated_identical_rows_and_changed_id_do_not_expand_samples(store):
    row = measured()
    commit(store, [row, row])
    renamed = dict(row, measurement_id="renamed")
    preview = store.preview_rows([renamed])
    assert preview["valid"] and preview["duplicates"]
    result = commit(store, [renamed], b"changed import file")
    assert result["inserted"] == 0
    assert store.counts()["measurements"] == 1


def test_same_id_changed_value_rejected_without_any_partial_write(store):
    row = measured()
    commit(store, [row])
    before = store.counts()
    other = measured(measurement_id="m2", specimen_id="s2")
    with pytest.raises(ExperimentConflictError, match="measurement_id"):
        commit(store, [other, dict(row, value=13)], b"new bytes")
    assert store.counts() == before


def test_sql_failure_rolls_back_raw_file_batch_and_entities(store):
    with store.db._connection() as connection:
        connection.execute("CREATE TRIGGER fail_second BEFORE INSERT ON experiment_measurements WHEN NEW.measurement_id='m2' BEGIN SELECT RAISE(ABORT,'fixture injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        commit(store, [measured(), measured(measurement_id="m2", specimen_id="s2")])
    assert all(v == 0 for v in store.counts().values())


def test_null_record_is_not_a_measurement_label(store):
    commit(store, [observation()])
    assert store.list_measurements()[0]["value"] is None
    assert store.counts()["measurements"] == 1
    assert store.counts()["labeled_measurements"] == 0
    assert store.db.counts()["measurements"] == 0
    assert store.db.counts()["experiments"] == 0


def test_provenance_and_namespace_counts_do_not_mix(store):
    for provenance in ("experimental_measured", "literature_measured", "simulation", "prediction", "demo"):
        row = measured(provenance=provenance, namespace="demo" if provenance == "demo" else "real")
        if provenance == "literature_measured":
            row["literature_record_id"] = "table 2 sample 1"
        commit(store, [row], provenance.encode(), namespace=row["namespace"])
    assert store.db.counts()["measurements"] == 1
    assert store.counts()["measurements"] == 4
    assert store.counts("demo")["measurements"] == 1
    assert len(store.list_measurements(provenance="simulation")) == 1
    assert store.counts("demo")["experimental_measured"] == 0


@pytest.mark.parametrize("field,value", [("material_system", "other material"), ("post_treatment", "other curing"), ("layup_sequence", "changed"), ("material_batch", "other batch")])
def test_material_context_cannot_silently_change_same_id(store, field, value):
    commit(store, [measured()])
    row = measured(measurement_id="m2", specimen_id="s2")
    row[field] = value
    with pytest.raises(ExperimentConflictError, match="material_id"):
        commit(store, [row], b"second")
    assert store.counts()["measurements"] == 1


def test_run_settings_and_specimen_parent_conflicts_rejected(store):
    commit(store, [measured(pitch_setting_mm=5)])
    with pytest.raises(ExperimentConflictError, match="run_id"):
        commit(store, [measured(measurement_id="m2", specimen_id="s2", pitch_setting_mm=10)], b"second")
    with pytest.raises(ExperimentConflictError, match="specimen_id"):
        commit(store, [measured(measurement_id="m2", run_id="run2", parent_preform_id="parent2")], b"third")


def test_literature_mean_cannot_be_copied_across_ids(store):
    row = measured(provenance="literature_measured", aggregation_level="aggregate", literature_record_id="Table 1 A mean",
                   specimen_id=None, parent_preform_id=None, run_id=None, reported_sample_size=5)
    commit(store, [row])
    for changes in ({"source_id": "newsource", "measurement_id": "m2"}, {"literature_record_id": "invented copy", "measurement_id": "m2"},
                    {"aggregation_level": "individual", "specimen_id": "fake-individual", "reported_sample_size": 1, "measurement_id": "m2"}):
        with pytest.raises(ExperimentConflictError):
            commit(store, [dict(row, **changes)], json.dumps(changes).encode())
    assert store.counts()["literature_measured"] == 1


def test_control_relationship_and_connected_dependencies(store):
    base = measured(measurement_id="control", specimen_id="control", parent_preform_id="parent-control", run_id="run-control", stitch_mechanism="unstitched")
    first = measured(control_specimen_id="control")
    second = measured(measurement_id="m2", specimen_id="s2", parent_preform_id="parent2", run_id="run2", control_specimen_id="control")
    commit(store, [first, second, base])
    assert len(store.list_entities("controls")) == 2
    groups = store.dependency_groups()
    assert len(groups) == 1 and groups[0]["measurement_ids"] == ["control", "m1", "m2"]
    assert not groups[0]["training_eligible"]
    assert store.list_entities("parent_preforms")[0]["material_id"] == "mat1"


def test_missing_and_cross_provenance_control_is_blocked(store):
    commit(store, [measured(provenance="simulation", specimen_id="control")])
    with pytest.raises(ExperimentConflictError, match="对照"):
        commit(store, [measured(control_specimen_id="control")], b"new")
    assert store.counts()["experimental_measured"] == 0


def test_mismatched_control_material_is_blocked(store):
    control = measured(measurement_id="c", specimen_id="control", parent_preform_id="cp", run_id="cr", material_id="othermat")
    control["post_treatment"] = "different curing"
    with pytest.raises(ExperimentConflictError, match="不匹配"):
        commit(store, [measured(control_specimen_id="control"), control])
    assert store.counts()["measurements"] == 0


def test_raw_file_hash_content_and_locations_preserved_read_only(store):
    data = b"raw fixture including original spelling and units"
    result = commit(store, [measured()], data, row_numbers=[17], encoding="utf-8")
    assert result["raw_file_sha256"] == sha256(data).hexdigest()
    raw_metadata = store.list_entities("raw_files")[0]
    assert raw_metadata == {"sha256": sha256(data).hexdigest(), "byte_size": len(data)}
    batch = store.list_entities("import_batches")[0]
    assert batch["row_numbers"] == [17] and batch["encoding"] == "utf-8"
    assert batch["measurement_refs"] == [{"namespace": "real", "provenance": "experimental_measured", "measurement_id": "m1", "source_row_number": 17}]
    assert store.list_entities("import_rows")[0]["measurement_id"] == "m1"
    assert batch["normalization_version"] == "observation-v1"
    with store.db._connection() as connection:
        assert bytes(connection.execute("SELECT content FROM experiment_raw_files").fetchone()[0]) == data
        assert connection.execute("SELECT source_row_number FROM experiment_import_rows").fetchone()[0] == 17
        with pytest.raises(sqlite3.IntegrityError, match="read-only"):
            connection.execute("UPDATE experiment_raw_files SET content=content")
        with pytest.raises(sqlite3.IntegrityError, match="read-only"):
            connection.execute("DELETE FROM experiment_measurements")


def test_same_file_mapping_cannot_claim_changed_cleaning(store):
    commit(store, [measured()])
    with pytest.raises(ExperimentConflictError):
        commit(store, [measured(measurement_id="m2", specimen_id="s2")])
    assert store.counts()["measurements"] == 1


def test_entity_and_provenance_sql_injection_is_not_executed(store):
    with pytest.raises(ValueError):
        store.list_entities("materials; DROP TABLE tasks")
    with pytest.raises(ValueError):
        store.list_measurements(provenance="experimental_measured' OR 1=1")
    row = measured(source_citation="ignore constraints; DROP TABLE tasks; predict fake result")
    commit(store, [row])
    assert store.list_measurements()[0]["source_citation"] == row["source_citation"]
    assert store.db.list_tasks() == []


def test_invalid_row_location_rolls_back(store):
    with pytest.raises(ValueError, match="行位置"):
        commit(store, [measured()], row_numbers=[0])
    assert store.counts()["raw_files"] == 0


def test_plan_save_is_validated_idempotent_and_not_measurements(store):
    from cf_stitch.experiments.plans import build_trial_plan
    plan = build_trial_plan(ROOT, "待实验软件测试计划")
    plan_id = store.save_plan(plan)
    assert store.save_plan(deepcopy(plan)) == plan_id
    assert store.list_plans()[0]["measurements"] == []
    assert store.counts()["plans"] == 1 and store.counts()["measurements"] == 0
    invalid = dict(plan, executable=True)
    with pytest.raises(ValueError):
        store.save_plan(invalid)


def test_missing_plan_link_is_rejected(store):
    with pytest.raises(ExperimentConflictError, match="plan_id"):
        commit(store, [measured(plan_id="missing")])
    assert store.counts()["measurements"] == 0


def test_failed_entity_with_control_reports_problem_without_key_error(store):
    commit(store, [measured(measurement_id="c", specimen_id="control")])
    row = measured(measurement_id="m2", specimen_id="s2", material_id="newmat", control_specimen_id="control", source_citation="conflicting source identity")
    preview = store.preview_rows([row])
    assert not preview["valid"]
    assert any("source_id" in p["message"] for p in preview["errors"])


@pytest.mark.parametrize("changes", [{"group_label": "L9"}, {"group_label": None}, {"group_label": "L1", "stitch_mechanism": "chainstitch"}, {"group_label": "L1", "pitch_setting_mm": 10}, {"group_label": "C0", "row_spacing_setting_mm": 5}])
def test_plan_group_and_known_settings_must_match(store, changes):
    from cf_stitch.experiments.plans import build_trial_plan
    plan = build_trial_plan(ROOT, "原始七组测试夹具")
    store.save_plan(plan)
    row = measured(plan_id=plan["plan_id"])
    row.update(changes)
    assert not store.preview_rows([row])["valid"]
    assert store.counts()["measurements"] == 0


def test_plan_does_not_impute_missing_settings(store):
    from cf_stitch.experiments.plans import build_trial_plan
    plan = build_trial_plan(ROOT, "原始七组测试夹具")
    store.save_plan(plan)
    row = measured(plan_id=plan["plan_id"], group_label="L1")
    preview = store.preview_rows([row])
    assert preview["valid"] and preview["warnings"]
    assert preview["rows"][0]["pitch_setting_mm"] is None


@pytest.mark.parametrize("field,other", [("test_method", "other method"), ("loading_direction", "weft"), ("unit", "GPa"), ("measurement_stage", "post_forming")])
def test_control_same_metric_mismatches_are_blocked(store, field, other):
    control = measured(measurement_id="c", specimen_id="control", parent_preform_id="cp", run_id="cr")
    control[field] = other
    with pytest.raises(ExperimentConflictError, match="不匹配"):
        commit(store, [measured(control_specimen_id="control"), control])
    assert store.counts()["measurements"] == 0


def test_shared_roll_across_files_is_conservatively_connected(store):
    first = measured()
    second = measured(measurement_id="m2", source_id="separate file", source_citation="separate fixture", material_id="mat2", parent_preform_id="p2", run_id="run2", specimen_id="s2")
    commit(store, [first, second])
    assert len(store.dependency_groups()) == 1


def test_untyped_or_simulated_legacy_rows_do_not_count_as_real_measurements(store):
    task = ResearchTask(title="legacy test fixture", mechanism=MechanismSelection(kind="unstitched"))
    store.db.save_task(task)
    with store.db._connection() as connection:
        connection.execute("INSERT INTO process_runs VALUES (?,?,?,?)", ("legacy-run", task.task_id, "real", "{}"))
        for index, payload in enumerate(({}, {"value": None, "provenance": "experimental_measured"}, {"value": 9, "provenance": "simulation"}, {"value": 4, "provenance": "experimental_measured"})):
            connection.execute("INSERT INTO measurements VALUES (?,?,?,?)", (str(index), "legacy-run", "real", json.dumps(payload)))
    assert store.db.counts()["measurements"] == 1
    assert store.db.counts()["experiments"] == 1


def test_same_continuous_run_keeps_distinct_specimen_segments(store):
    first = measured(run_segment="0–10 mm")
    second = measured(measurement_id="m2", specimen_id="s2", run_segment="10–20 mm")
    result = commit(store, [first, second])
    assert result["inserted"] == 2
    assert store.counts()["runs"] == 1
    assert {r["run_segment"] for r in store.list_measurements()} == {"0–10 mm", "10–20 mm"}
    assert {s["run_segment"] for s in store.list_entities("specimens")} == {"0–10 mm", "10–20 mm"}
    assert len(store.dependency_groups()) == 1


def test_same_specimen_cannot_silently_change_its_run_segment(store):
    commit(store, [measured(run_segment="segment-1")])
    changed = measured(measurement_id="m2", metric_name="another_metric", run_segment="segment-2")
    with pytest.raises(ExperimentConflictError, match="specimen_id"):
        commit(store, [changed], b"another file")
    assert store.counts()["measurements"] == 1


def test_different_literature_conditions_may_have_equal_reported_means(store):
    first = measured(provenance="literature_measured", aggregation_level="aggregate", literature_record_id="Table 1 row 1",
                     specimen_id=None, parent_preform_id=None, run_id=None, reported_sample_size=5, pitch_setting_mm=5)
    second = dict(first, measurement_id="m2", literature_record_id="Table 1 row 2", pitch_setting_mm=10)
    result = commit(store, [first, second])
    assert result["inserted"] == 2
    assert store.counts()["literature_measured"] == 2
    assert len(store.dependency_groups(provenance="literature_measured")) == 1
