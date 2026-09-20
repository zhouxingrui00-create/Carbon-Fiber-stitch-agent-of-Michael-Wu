"""Isolated Stage 4 Streamlit interactions; no true experimental data."""

import csv
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from cf_stitch.storage.database import Database
from cf_stitch.storage.experiments import ExperimentStore
from cf_stitch.knowledge.parameters import ManualParameterConfirmation
from cf_stitch.services.evidence import index_local_evidence


ROOT = Path(__file__).resolve().parents[1]


def csv_bytes(rows):
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def blank_observation(**changes):
    return {
        "measurement_id": "test-M1", "source_id": "test-source", "source_citation": "隔离软件夹具，非真实实验",
        "provenance": "experimental_measured", "material_id": "test-mat", "material_system": "软件夹具",
        "material_batch": "test-batch", "roll_id": "test-roll", "parent_preform_id": "test-parent",
        "run_id": "test-run", "run_segment": "test-segment", "specimen_id": "test-specimen",
        "specimen_origin": "软件验收夹具", "metric_name": "ILSS", "value": "", "unit": "MPa",
        "measurement_stage": "final_testing", "test_method": "仅软件测试方法", "loading_direction": "夹具方向",
        "post_treatment": "夹具后处理", "missing_reason": "尚未试验",
        "stitch_mechanism": "lockstitch", "pitch_setting_mm": "5", "row_spacing_setting_mm": "10",
        **changes,
    }


@pytest.fixture
def app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("CF_STITCH_DATA_DIR", str(tmp_path))
    holder = {"file": None}
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: holder["file"])
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=45).run()
    assert not app.exception
    database = Database(tmp_path / "cf_stitch.sqlite3")
    return app, database, ExperimentStore(database), holder


def upload_page(app, holder, payload, name="fixture.csv"):
    holder["file"] = SimpleNamespace(name=name, getvalue=lambda: payload)
    app.sidebar.radio(key="navigation").set_value("数据管理").run()
    app.radio(key="experiment_data_page").set_value("导入预览").run()
    assert not app.exception


def acknowledge_and_commit(app):
    checkbox = next(item for item in app.checkbox if item.key.startswith("ack_import_"))
    checkbox.check().run()
    app.button(key="commit_experiment_import").click().run()
    assert not app.exception


def test_plan_is_seven_pending_groups_and_persisted_without_measurements(app_data):
    app, db, store, _ = app_data
    app.sidebar.radio(key="navigation").set_value("实验设计").run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 7
    assert not store.list_plans()
    app.button(key="save_trial_plan").click().run()
    assert any("计划名称" in item.value for item in app.error)
    app.text_input(key="plan_title").set_value("隔离软件验收计划")
    app.button(key="save_trial_plan").click().run()
    assert not app.exception and app.success
    plans = store.list_plans()
    assert len(plans) == 1
    assert [group["source_group_label"] for group in plans[0]["groups"]] == ["C0", "L1", "L2", "L3", "C1", "C2", "C3"]
    assert all(group["measurements"] == [] for group in plans[0]["groups"])
    assert store.list_measurements() == []
    assert db.counts()["experiments"] == db.counts()["measurements"] == 0
    reloaded = AppTest.from_file(str(ROOT / "app.py"), default_timeout=45).run()
    reloaded.sidebar.radio(key="navigation").set_value("实验设计").run()
    assert not reloaded.exception and reloaded.selectbox(key="plan_review_selection")


def test_preview_is_read_only_missing_value_stays_null_and_repeat_is_idempotent(app_data):
    app, db, store, holder = app_data
    data = csv_bytes([blank_observation()])
    upload_page(app, holder, data)
    assert not store.list_measurements()
    app.button(key="preview_experiment_import").click().run()
    assert not app.exception and not app.error
    assert not store.list_measurements()
    assert app.button(key="commit_experiment_import").disabled
    acknowledge_and_commit(app)
    assert app.success
    rows = store.list_measurements()
    assert len(rows) == 1 and rows[0]["value"] is None and rows[0]["missing_reason"] == "尚未试验"
    assert rows[0]["pitch_setting_mm"] == 5
    assert db.counts()["measurements"] == 0
    app.button(key="preview_experiment_import").click().run()
    assert not app.exception
    acknowledge_and_commit(app)
    assert len(store.list_measurements()) == 1


def test_one_invalid_row_prevents_whole_batch(app_data):
    app, _, store, holder = app_data
    invalid = blank_observation(measurement_id="test-bad", specimen_id="test-bad-specimen", value="NaN", missing_reason="")
    upload_page(app, holder, csv_bytes([blank_observation(), invalid]))
    app.button(key="preview_experiment_import").click().run()
    assert not app.exception
    assert any("不能提交" in item.value for item in app.error)
    assert not any(item.key == "commit_experiment_import" for item in app.button)
    assert not store.list_measurements()


def test_mapping_change_invalidates_review_and_requires_new_preview(app_data):
    app, _, store, holder = app_data
    upload_page(app, holder, csv_bytes([blank_observation()]))
    app.button(key="preview_experiment_import").click().run()
    assert any(item.key == "commit_experiment_import" for item in app.button)
    unit_map = next(item for item in app.selectbox if item.key.startswith("map_") and item.label == "unit")
    unit_map.select("__ignore__").run()
    assert not app.exception
    assert not any(item.key == "commit_experiment_import" for item in app.button)
    assert not store.list_measurements()


def test_file_change_invalidates_previous_preview(app_data):
    app, _, store, holder = app_data
    upload_page(app, holder, csv_bytes([blank_observation()]))
    app.button(key="preview_experiment_import").click().run()
    other = csv_bytes([blank_observation(measurement_id="different-file-M")])
    holder["file"] = SimpleNamespace(name="different.csv", getvalue=lambda: other)
    app.run()
    assert not app.exception
    assert not any(item.key == "commit_experiment_import" for item in app.button)
    assert not store.list_measurements()


def test_demo_rejected_from_real_then_imports_in_demo_only(app_data):
    app, db, store, holder = app_data
    payload = csv_bytes([blank_observation(provenance="demo")])
    upload_page(app, holder, payload)
    app.button(key="preview_experiment_import").click().run()
    assert any("不能提交" in item.value for item in app.error)
    app.selectbox(key="experiment_namespace").select("demo").run()
    app.button(key="preview_experiment_import").click().run()
    assert not app.exception and not app.error
    acknowledge_and_commit(app)
    assert len(store.list_measurements(namespace="demo")) == 1
    assert store.list_measurements(namespace="real") == []
    assert db.counts(namespace="real")["measurements"] == 0


def test_unmapped_nonempty_column_is_visible_and_never_silently_dropped(app_data):
    app, _, store, holder = app_data
    payload = csv_bytes([blank_observation(未识别列="不能静默丢弃")])
    upload_page(app, holder, payload)
    app.button(key="preview_experiment_import").click().run()
    assert any("不能提交" in item.value for item in app.error)
    unknown_map = next(item for item in app.selectbox if item.label == "未识别列")
    unknown_map.select("__ignore__").run()
    app.button(key="preview_experiment_import").click().run()
    assert not app.exception
    assert not store.list_measurements()


def test_empty_template_upload_does_not_create_sample_or_zero(app_data):
    app, _, store, holder = app_data
    upload_page(app, holder, b"measurement_id,source_id,provenance,value,unit\r\n")
    app.button(key="preview_experiment_import").click().run()
    assert not app.exception
    assert any("不能提交" in item.value for item in app.error)
    assert store.list_measurements() == []


def test_xlsx_can_select_data_after_empty_first_sheet(app_data):
    # Tiny OOXML fixture tests the parser contract, not workbook authoring.
    from test_experiment_importing import sheet, xlsx
    app, _, store, holder = app_data
    row = blank_observation()
    payload = xlsx({"空说明": sheet([]), "数据": sheet([list(row), list(row.values())])})
    upload_page(app, holder, payload, "two-sheets.xlsx")
    assert app.error
    assert app.selectbox(key="experiment_sheet").options == ["空说明", "数据"]
    app.selectbox(key="experiment_sheet").select("数据").run()
    app.button(key="preview_experiment_import").click().run()
    assert not app.exception and not app.error
    acknowledge_and_commit(app)
    assert store.list_measurements()[0]["value"] is None
    app.selectbox(key="experiment_sheet").select("空说明").run()
    assert app.error and not any(item.key == "commit_experiment_import" for item in app.button)


def test_plan_device_limit_requires_explicit_applicability_and_blocks(app_data):
    app, db, store, _ = app_data
    index_local_evidence(ROOT, db)
    source = next(item for item in db.list_parameters() if item["record_id"] == "D1-DUAL-H6")
    confirmation = ManualParameterConfirmation(
        source_record_id=source["record_id"], field=source["field"], unit=source["unit"],
        equipment_name="软件验收设备", review_scope="隔离测试材料压实总厚度上限",
        reviewer="软件验收", reason="验证阻断，非真实能力", confirmed_at=datetime.now(timezone.utc),
        max_value=6, source_refs=source["source_refs"],
    )
    db.save_parameter_confirmation(confirmation)
    app.sidebar.radio(key="navigation").set_value("实验设计").run()
    app.text_input(key="plan_title").set_value("隔离设备审查计划")
    app.button(key="save_trial_plan").click().run()
    assert not app.exception
    plan = store.list_plans()[0]
    assert app.selectbox(key="plan_equipment_confirmation").value is None
    app.selectbox(key="plan_equipment_confirmation").select(confirmation.confirmation_id).run()
    assert json.loads(app.json[-1].value)["status"] == "UNKNOWN"
    app.checkbox(key=f"plan_limit_applies_{plan['plan_id']}_{confirmation.confirmation_id}").check().run()
    report = json.loads(app.json[-1].value)
    assert report["status"] == "BLOCK" and report["executable"] is False
    assert store.list_measurements() == [] and store.counts()["runs"] == 0
    assert store.list_plans()[0] == plan
