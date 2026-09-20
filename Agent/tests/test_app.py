"""Streamlit AppTest checks; these are not real-browser end-to-end tests."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from cf_stitch.storage.database import Database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app_context(monkeypatch, tmp_path):
    monkeypatch.setenv("CF_STITCH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    database = Database(tmp_path / "cf_stitch.sqlite3")
    return app, database


def test_offline_pages_render_and_keep_real_data_empty(app_context):
    app, database = app_context
    assert "机器人" not in app.selectbox(key="task_mechanism").options
    assert "机器人" in app.selectbox(key="task_platform").options
    assert "人字形" in app.selectbox(key="task_path").options
    assert len(database.list_parameters()) == 33
    assert database.counts() == {"tasks": 0, "experiments": 0, "measurements": 0, "trained_models": 0, "confirmed_equipment": 0}
    for page in ["资料参数", "实验设计", "数据管理", "设置", "任务工作台"]:
        app.sidebar.radio(key="navigation").set_value(page).run()
        assert not app.exception
        if page == "资料参数":
            assert len(app.dataframe[0].value) == 33
            assert any("哈希" in warning.value for warning in app.warning)
            app.selectbox(key="parameter_document").select("D2").run()
            assert len(app.dataframe[0].value) == 12
            app.selectbox(key="parameter_scope").select("j_beam").run()
            assert any("没有来源参数" in info.value for info in app.info)
        if page == "实验设计":
            assert len(app.dataframe[0].value) == 7
            assert app.button(key="save_trial_plan")
            assert database.counts()["experiments"] == 0
        if page == "数据管理":
            assert "not_ready" in app.code[0].value
    assert database.counts()["experiments"] == 0
    assert database.counts()["measurements"] == 0


def test_create_draft_and_reload_from_database(app_context):
    app, database = app_context
    app.text_input(key="task_title").set_value("界面持久化测试")
    app.text_input(key="task_material").set_value("C/C")
    app.text_input(key="task_configuration").set_value("厚预制体研发试件")
    app.text_input(key="task_objective").set_value("孔隙率")
    app.selectbox(key="task_objective_direction").select("minimize")
    app.text_input(key="task_objective_unit").set_value("%")
    app.selectbox(key="task_mechanism").select("custom_unconfirmed")
    app.selectbox(key="task_path").select("herringbone")
    app.selectbox(key="task_platform").select("robot")
    app.text_input(key="task_pitch").set_value("5")
    app.button[0].click().run()
    assert not app.exception
    assert app.success
    records = database.list_tasks()
    assert len(records) == 1
    task = records[0]
    assert task["title"] == "界面持久化测试"
    assert task["material"]["material_system"] == "C/C"
    assert task["geometry"]["configuration"] == "厚预制体研发试件"
    assert task["objectives"][0]["metric_name"] == "孔隙率"
    assert task["objectives"][0]["target"] is None
    assert task["mechanism"] == {"kind": "custom_unconfirmed", "raw_mechanism_name": "无底线自锁"}
    assert task["path_pattern"] == "herringbone"
    assert task["platform"] == "robot"
    assert task["parameters"]["pitch_mm"]["value"] == 5
    assert task["parameters"]["row_spacing_mm"]["value"] is None
    assert task["parameters"]["row_spacing_mm"]["missing_reason"]
    reloaded = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30).run()
    assert not reloaded.exception
    assert reloaded.dataframe[0].value.iloc[0]["任务名称"] == "界面持久化测试"
    assert len(database.list_tasks()) == 1
    assert database.counts()["measurements"] == 0


def test_empty_title_and_negative_pitch_are_rejected(app_context):
    app, database = app_context
    app.button[0].click().run()
    assert any("任务名称不能为空" in error.value for error in app.error)
    assert database.counts()["tasks"] == 0
    app.text_input(key="task_title").set_value("负针距验证")
    app.selectbox(key="task_mechanism").select("lockstitch")
    app.text_input(key="task_pitch").set_value("-1")
    app.button[0].click().run()
    assert not app.exception
    assert any("校验不通过" in error.value for error in app.error)
    assert database.counts()["tasks"] == 0


def test_unstitched_missing_spacing_is_saved_without_discarding_values(app_context):
    app, database = app_context
    app.text_input(key="task_title").set_value("未缝合对照")
    app.selectbox(key="task_mechanism").select("unstitched")
    app.text_input(key="task_pitch").set_value("5")
    app.button[0].click().run()
    assert any("不会丢弃" in error.value for error in app.error)
    assert database.counts()["tasks"] == 0
    assert app.text_input(key="task_pitch").value == "5"
    app.text_input(key="task_pitch").set_value("")
    app.button[0].click().run()
    assert not app.exception
    assert app.success
    parameters = database.list_tasks()[0]["parameters"]
    assert parameters["pitch_mm"]["value"] is None
    assert parameters["row_spacing_mm"]["value"] is None
    assert parameters["pitch_mm"]["missing_reason"] == "not_applicable"


def test_invalid_data_directory_reports_actionable_initialization_error(monkeypatch):
    monkeypatch.setenv("CF_STITCH_DATA_DIR", "src")
    app = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    assert any("初始化失败" in error.value and "CF_STITCH_DATA_DIR" in error.value for error in app.error)
    assert not app.success
    assert not app.text_input
    assert not app.button
