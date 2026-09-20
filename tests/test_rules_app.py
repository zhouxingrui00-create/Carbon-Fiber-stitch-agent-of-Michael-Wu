"""Stage 3 AppTest coverage; not a claim of browser or machine validation."""

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from cf_stitch.domain import ResearchTask
from cf_stitch.knowledge.parameters import ManualParameterConfirmation
from cf_stitch.services.evidence import index_local_evidence
from cf_stitch.storage.database import Database


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def rules_app(monkeypatch, tmp_path):
    monkeypatch.setenv("CF_STITCH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    app.sidebar.radio(key="navigation").set_value("三场景与审查").run()
    assert not app.exception
    return app, Database(tmp_path / "cf_stitch.sqlite3")


def result(app):
    assert not app.exception
    return json.loads(app.json[-1].value)


def calculation(app, name):
    app.radio(key="rules_page").set_value("条件化计算").run()
    app.selectbox(key="rules_calculation").select(name).run()
    assert not app.exception


def test_scenario_cards_preserve_separate_sources_and_pending_states(rules_app):
    app, db = rules_app
    for scenario in ("general_research", "v_splice", "j_beam", "dual_layer_fixation"):
        app.selectbox(key="rules_scenario").select(scenario).run()
        card = result(app)
        assert card["key"] == scenario
        assert card["reachability_status"] == "UNKNOWN"
        assert card["collision_status"] == "UNKNOWN"
        assert card["candidate_approval"] is False
        assert all(item["default"] is None for item in card["context_schema"])
        assert all(item["verification_status"] == "source_only" for item in card["claims"])
    claims = {item["record_id"]: item for item in card["claims"]}
    assert (claims["D1-DUAL-P"]["min"], claims["D1-DUAL-P"]["max"]) == (10, 30)
    assert claims["D1-DUAL-H5"]["max"] == 5
    assert claims["D1-DUAL-H6"]["max"] == 6
    assert claims["D1-DUAL-P"]["evidence_blocks"]
    assert db.counts()["tasks"] == 0


@pytest.mark.parametrize("pitch,spacing,expected", [(5, 5, 40000), (5, 10, 20000), (10, 10, 10000), (10, 20, 5000)])
def test_density_requires_explicit_assumptions_and_returns_theoretical_values(rules_app, pitch, spacing, expected):
    app, db = rules_app
    calculation(app, "矩形阵列理论密度")
    assert app.text_input(key="density_pitch").value == ""
    assert not app.checkbox(key="density_rectangular").value
    app.selectbox(key="density_mechanism").select("lockstitch")
    app.selectbox(key="density_path").select("parallel")
    app.text_input(key="density_pitch").set_value(str(pitch))
    app.text_input(key="density_spacing").set_value(str(spacing))
    app.button(key="rules_compute").click().run()
    assert result(app)["value"] is None
    app.checkbox(key="density_rectangular").check()
    app.checkbox(key="density_one_puncture").check()
    app.checkbox(key="density_no_extras").check()
    app.button(key="rules_compute").click().run()
    computed = result(app)
    assert computed["value"] == expected
    assert computed["source_refs"]
    assert computed["assumptions"]
    assert db.counts()["measurements"] == 0


def test_unstitched_empty_density_and_complex_path_guards(rules_app):
    app, _ = rules_app
    calculation(app, "矩形阵列理论密度")
    app.selectbox(key="density_mechanism").select("unstitched")
    app.button(key="rules_compute").click().run()
    assert result(app)["value"] == 0
    app.text_input(key="density_pitch").set_value("0")
    app.button(key="rules_compute").click().run()
    assert result(app)["value"] is None
    app.selectbox(key="density_mechanism").select("lockstitch")
    app.selectbox(key="density_path").select("herringbone")
    app.text_input(key="density_pitch").set_value("5")
    app.text_input(key="density_spacing").set_value("5")
    for key in ("density_rectangular", "density_one_puncture", "density_no_extras"):
        app.checkbox(key=key).check()
    app.button(key="rules_compute").click().run()
    assert result(app)["value"] is None


def test_speed_and_frequency_conversion_require_cycle_assumption(rules_app):
    app, _ = rules_app
    calculation(app, "理想直线送料速度")
    app.text_input(key="speed_pitch").set_value("5")
    app.text_input(key="speed_frequency").set_value("100")
    app.button(key="rules_compute").click().run()
    assert result(app)["value"] is None
    for key in ("speed_straight", "speed_feed_advance", "speed_one_cycle"):
        app.checkbox(key=key).check()
    app.button(key="rules_compute").click().run()
    assert result(app)["value"] == 0.5
    calculation(app, "单位换算")
    app.text_input(key="unit_value").set_value("10")
    app.selectbox(key="unit_from").select("Hz")
    app.selectbox(key="unit_to").select("stitches/min")
    app.button(key="rules_compute").click().run()
    assert result(app)["value"] is None
    app.checkbox(key="unit_one_cycle").check()
    app.button(key="rules_compute").click().run()
    computed = result(app)
    assert computed["value"] == 600
    assert computed["details"]


def test_j_density_stays_pending_and_control_is_not_implicitly_matched(rules_app):
    app, db = rules_app
    calculation(app, "J 型针密条件换算")
    app.button(key="rules_compute").click().run()
    assert result(app)["value"] is None
    app.checkbox(key="j_density_single_row").check()
    app.button(key="rules_compute").click().run()
    computed = result(app)
    assert computed["status"] == "WARN"
    assert computed["value"] == pytest.approx([2 / 3, 2])
    calculation(app, "匹配对照提升率 / 损失率")
    app.text_input(key="change_stitched").set_value("12")
    app.text_input(key="change_control").set_value("10")
    app.text_input(key="change_unit").set_value("N")
    app.button(key="rules_compute").click().run()
    assert result(app)["value"] is None
    app.checkbox(key="change_matched").check()
    app.selectbox(key="change_mode").select("loss")
    app.button(key="rules_compute").click().run()
    assert result(app)["value"] == -20
    app.text_input(key="change_control").set_value("0")
    app.button(key="rules_compute").click().run()
    assert result(app)["value"] is None
    assert db.counts()["measurements"] == 0


def test_extended_task_fields_remain_distinct_and_persist_as_draft(rules_app):
    app, db = rules_app
    app.sidebar.radio(key="navigation").set_value("任务工作台").run()
    app.text_input(key="task_title").set_value("仅软件验收的双层草案")
    app.selectbox(key="task_scenario").select("dual_layer_fixation")
    app.selectbox(key="task_mechanism").select("chainstitch")
    values = {
        "task_thickness": "22", "task_thickness_condition": "软件夹具，假设压实状态",
        "task_pitch": "20", "task_spacing": "10", "task_mechanical_spacing": "120",
        "task_needle_depth": "22", "task_foot_lift": "30", "task_tension": "2",
        "task_upper_web_tension": "40", "task_lower_web_tension": "50",
        "task_frequency": "200", "task_line_speed": "4",
    }
    for key, value in values.items():
        app.text_input(key=key).set_value(value)
    app.button[0].click().run()
    assert not app.exception
    assert app.success
    saved = db.list_tasks()[0]
    assert saved["material"]["compacted_thickness_mm"]["value"] == 22
    assert saved["material"]["compacted_thickness_mm"]["value_kind"] == "context"
    parameters = saved["parameters"]
    expected = {"pitch_mm": 20, "row_spacing_mm": 10, "mechanical_needle_spacing_mm": 120, "needle_depth_mm": 22, "foot_lift_mm": 30, "yarn_feed_tension_N": 2, "upper_fabric_web_tension_N": 40, "lower_fabric_web_tension_N": 50, "stitch_frequency_spm": 200, "line_speed_m_min": 4}
    for field, value in expected.items():
        assert parameters[field]["value"] == value
        assert parameters[field]["value_kind"] == "setting"
    app.sidebar.radio(key="navigation").set_value("三场景与审查").run()
    app.radio(key="rules_page").set_value("已存任务审查").run()
    report = result(app)
    assert report["status"] == "UNKNOWN"
    assert report["candidate_state"] == "draft"
    assert report["executable"] is False
    assert not any(item["status"] == "BLOCK" and item.get("field") == "pitch_mm" for item in report["findings"])
    assert db.counts()["measurements"] == 0


def test_confirmed_six_mm_limit_blocks_twenty_two_only_after_explicit_selection(rules_app):
    app, db = rules_app
    task = ResearchTask.model_validate({
        "title": "隔离软件夹具 22 mm", "scenario": "dual_layer_fixation",
        "mechanism": {"kind": "chainstitch"},
        "material": {"compacted_thickness_mm": {"value": 22, "unit": "mm", "value_kind": "context"}, "thickness_condition": "软件测试假设"},
    })
    db.save_task(task)
    index_local_evidence(ROOT, db)
    source = next(item for item in db.list_parameters() if item["record_id"] == "D1-DUAL-H6")
    confirmation = ManualParameterConfirmation(
        source_record_id=source["record_id"], field=source["field"], unit=source["unit"],
        equipment_name="仅用于软件测试的隔离设备", review_scope="隔离夹具的压实总厚度上限，不用于真实生产",
        reviewer="软件测试", reason="验证设备阻断流程，非实测结果", confirmed_at=datetime.now(timezone.utc),
        max_value=6, source_refs=source["source_refs"],
    )
    db.save_parameter_confirmation(confirmation)
    app.radio(key="rules_page").set_value("已存任务审查").run()
    assert result(app)["candidate_state"] == "draft"
    assert app.selectbox(key="review_confirmation").value is None
    app.selectbox(key="review_confirmation").select(confirmation.confirmation_id).run()
    assert result(app)["candidate_state"] == "draft"
    app.checkbox(key=f"review_confirmation_applies_{task.task_id}_{confirmation.confirmation_id}").check().run()
    report = result(app)
    assert report["status"] == "BLOCK"
    assert report["candidate_state"] == "blocked"
    assert report["executable"] is False
    assert any(item["status"] == "BLOCK" and item["source_refs"] for item in report["findings"])
    assert db.counts()["confirmed_equipment"] == 0
    assert db.list_tasks()[0]["status"] == "draft"


def test_general_startup_frequency_window_needs_explicit_context(rules_app):
    app, db = rules_app
    task = ResearchTask.model_validate({
        "title": "隔离测试的通用针频草案", "scenario": "general_research",
        "mechanism": {"kind": "lockstitch"},
        "parameters": {"stitch_frequency_spm": {"value": 500, "unit": "stitches/min"}},
    })
    db.save_task(task)
    app.radio(key="rules_page").set_value("已存任务审查").run()

    def startup_findings():
        return [finding for finding in result(app)["findings"] if finding.get("field") == "stitch_frequency_spm" and any(ref.get("document_id") == "D2" and ref.get("block_id") == "D2:t002" for ref in finding["source_refs"])]

    assert any(item["status"] == "UNKNOWN" for item in startup_findings())
    assert not app.checkbox(key=f"review_research_startup_{task.task_id}").value
    app.checkbox(key=f"review_research_startup_{task.task_id}").check().run()
    assert any(item["status"] == "WARN" for item in startup_findings())
    assert not any(item["status"] == "BLOCK" for item in startup_findings())
    assert result(app)["executable"] is False


def test_missing_scenario_evidence_is_visible_and_no_data_are_generated(rules_app, monkeypatch):
    app, db = rules_app

    def unavailable(*args, **kwargs):
        raise ValueError("验收：场景来源不可读取")

    monkeypatch.setattr("cf_stitch.rules_ui.get_scenario", unavailable)
    app.run()
    assert not app.exception
    assert any("来源无法读取" in item.value for item in app.error)
    assert not app.dataframe
    app.radio(key="rules_page").set_value("已存任务审查").run()
    assert not app.exception
    assert any("尚无已保存任务" in item.value for item in app.info)
    assert all(value == 0 for value in db.counts().values())
