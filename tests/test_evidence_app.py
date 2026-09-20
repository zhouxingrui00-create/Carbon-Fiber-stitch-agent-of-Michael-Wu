"""Stage 2 UI checks with isolated SQLite files; not browser coverage."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from cf_stitch.knowledge.seeds import load_parameter_seed
from cf_stitch.storage.database import Database


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def knowledge_app(monkeypatch, tmp_path):
    monkeypatch.setenv("CF_STITCH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio(key="navigation").set_value("资料参数").run()
    assert not app.exception
    return app, Database(tmp_path / "cf_stitch.sqlite3")


def test_dictionary_keeps_distinct_tension_and_spacing_objects(knowledge_app):
    app, db = knowledge_app
    app.text_input(key="dictionary_query").set_value("张力").run()
    ids = set(app.dataframe[0].value["记录 ID"])
    assert {"D2-T", "D2-T-FINE", "D1-DUAL-T"} <= ids
    app.text_input(key="dictionary_query").set_value("针距").run()
    ids = set(app.dataframe[0].value["记录 ID"])
    assert {"D2-P", "D2-S", "D1-DUAL-SPACING"} <= ids
    assert db.list_parameters() == load_parameter_seed(ROOT)["records"]
    assert not db.list_parameter_confirmations()


def test_search_has_literal_evidence_and_empty_results(knowledge_app):
    app, db = knowledge_app
    app.radio(key="knowledge_page").set_value("证据检索").run()
    app.text_input(key="evidence_query").set_value("张力").run()
    assert not app.exception
    assert len(app.dataframe[0].value) > 1
    assert any("张力" in item.value for item in app.text)
    assert any(len(item.value) == 64 for item in app.code)
    app.text_input(key="evidence_query").set_value("不存在的实验结果ZZ991").run()
    assert not app.exception
    assert any("未找到证据" in item.value for item in app.info)
    assert not app.dataframe
    assert not app.code
    assert db.counts()["measurements"] == 0


def test_versions_show_unparsed_images_and_exact_missing_location(knowledge_app):
    app, db = knowledge_app
    app.radio(key="knowledge_page").set_value("来源审查").run()
    assert not app.exception
    versions = db.list_document_versions()
    assert len(versions) == 3  # Actual D1, actual D2, and declared D1 JSON.
    assert {item["identity_status"] for item in versions} == {"mismatch", "matched", "declared_only"}
    app.text_input(key="source_block_id").set_value("D1:does-not-exist").run()
    assert any("未找到证据" in item.value for item in app.info)


def test_pending_reviews_are_unresolved_and_citations_resolve(knowledge_app):
    app, db = knowledge_app
    app.radio(key="knowledge_page").set_value("待确认").run()
    assert not app.exception
    issue_headers = [expander.label for expander in app.expander if expander.label.startswith("待确认 ·")]
    assert len(issue_headers) == 6
    assert not any("未找到此文件" in warning.value for warning in app.warning)
    assert not app.button
    assert not db.list_parameter_confirmations()


def test_manual_confirmation_is_explicit_and_separate(knowledge_app):
    app, db = knowledge_app
    original = db.list_parameters()
    app.radio(key="knowledge_page").set_value("人工确认版本").run()
    assert not app.exception
    app.button[0].click().run()
    assert app.error
    assert not db.list_parameter_confirmations()
    app.selectbox(key="confirmation_record").select("D2-P")
    app.text_input(key="confirmation_equipment").set_value("仅软件测试设备，隔离数据库")
    app.text_input(key="confirmation_min").set_value("4")
    app.text_input(key="confirmation_max").set_value("8")
    app.text_input(key="confirmation_reviewer").set_value("软件测试审核员")
    app.text_input(key="confirmation_scope").set_value("软件验证用合成范围；不能上机")
    app.text_area(key="confirmation_reason").set_value("仅验证保存流程，不是实验或实际设备依据")
    app.checkbox(key="confirmation_ack").check()
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    assert app.success
    confirmation = db.list_parameter_confirmations()[0]
    assert confirmation["reviewer"] == "软件测试审核员"
    assert confirmation["min_value"] == 4
    assert confirmation["max_value"] == 8
    assert db.list_parameters() == original
    assert all(value == 0 for value in db.counts().values())
    reopened = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    reopened.sidebar.radio(key="navigation").set_value("资料参数").run()
    reopened.radio(key="knowledge_page").set_value("人工确认版本").run()
    assert any("人工确认版本（1）" in item.value for item in reopened.subheader)


def test_existing_seed_database_keeps_navigation_when_sources_are_unavailable(knowledge_app, monkeypatch):
    from cf_stitch.knowledge.documents import DocumentReadError
    from cf_stitch.knowledge.seeds import SeedValidationError

    app, db = knowledge_app
    source_records = db.list_parameters()
    historical_versions = db.list_document_versions()

    def unreadable_seed(self, project_root):
        raise SeedValidationError("测试缺证据：种子关联的提取文件当前无法读取")

    def unreadable_document(*args, **kwargs):
        raise DocumentReadError("测试缺证据：当前原件与后备均无法读取")

    monkeypatch.setattr(Database, "import_seed_bundle", unreadable_seed)
    monkeypatch.setattr("cf_stitch.services.evidence.load_document", unreadable_document)
    app.sidebar.radio(key="navigation").set_value("任务工作台").run()
    assert not app.exception
    assert app.text_input(key="task_title")
    app.sidebar.radio(key="navigation").set_value("资料参数").run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 33
    assert any("无法读取" in item.value for item in app.error)
    app.radio(key="knowledge_page").set_value("证据检索").run()
    app.text_input(key="evidence_query").set_value("针距").run()
    assert not app.exception
    assert any("未找到证据" in item.value for item in app.info)
    assert not app.dataframe
    assert not app.code
    assert db.list_parameters() == source_records
    assert db.list_document_versions() == historical_versions
    # Historical evidence is available only through explicit history navigation;
    # a failed current read cannot silently populate the current search results.
    app.radio(key="knowledge_page").set_value("来源审查").run()
    assert not app.exception
    assert app.selectbox(key="source_version")


def test_empty_database_without_source_seed_can_save_a_minimal_draft(monkeypatch, tmp_path):
    from cf_stitch.knowledge.documents import DocumentReadError
    from cf_stitch.knowledge.seeds import SeedValidationError

    monkeypatch.setenv("CF_STITCH_DATA_DIR", str(tmp_path))

    def unreadable_seed(self, project_root):
        raise SeedValidationError("测试缺证据：没有可读取的来源种子")

    def unreadable_document(*args, **kwargs):
        raise DocumentReadError("测试缺证据：没有可读取的资料")

    monkeypatch.setattr(Database, "import_seed_bundle", unreadable_seed)
    monkeypatch.setattr("cf_stitch.services.evidence.load_document", unreadable_document)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    app.text_input(key="task_title").set_value("缺来源时的独立草案测试")
    app.selectbox(key="task_mechanism").select("unstitched")
    app.button[0].click().run()
    assert not app.exception
    assert app.success
    db = Database(tmp_path / "cf_stitch.sqlite3")
    assert db.list_parameters() == []
    assert db.list_document_versions() == []
    assert db.counts()["tasks"] == 1
    assert db.counts()["measurements"] == 0
    task = db.list_tasks()[0]
    assert task["parameters"]["pitch_mm"]["value"] is None
    assert task["parameters"]["row_spacing_mm"]["value"] is None
    assert task["parameters"]["pitch_mm"]["source_refs"] == []
    app.sidebar.radio(key="navigation").set_value("资料参数").run()
    assert not app.exception
    assert any("没有来源参数" in item.value for item in app.info)
    app.radio(key="knowledge_page").set_value("证据检索").run()
    app.text_input(key="evidence_query").set_value("针距").run()
    assert any("未找到证据" in item.value for item in app.info)
    assert not app.code
