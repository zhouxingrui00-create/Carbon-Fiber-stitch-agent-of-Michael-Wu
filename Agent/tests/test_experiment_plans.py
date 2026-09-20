"""Plans are source-backed drafts, never invented measurements or equipment approvals."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from cf_stitch.experiments.plans import (
    REQUIRED_CONDITIONS, build_trial_plan, review_plan, validate_plan,
)
from cf_stitch.knowledge.seeds import load_experiment_template
from cf_stitch.rules.results import Status


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def plan():
    return build_trial_plan(ROOT, "待实验计划，未执行")


def equipment(field="stable_sewing_thickness_mm", maximum=6):
    return {"equipment_id": "test-only", "equipment_name": "隔离测试夹具",
            "approved_by": "测试夹具", "approved_at": "2026-09-20T00:00:00+00:00",
            "approval_reason": "非真实设备确认，仅检查软件",
            "limits": [{"field": field, "unit": "mm", "max_value": maximum,
                        "applicability": "当前测试条件", "evidence_note": "合成测试边界"}]}


def test_source_groups_and_context_are_preserved_exactly(plan):
    source = load_experiment_template(ROOT)
    for name in ("groups", "applicability", "source_refs", "warning", "additional_research_design_suggestions"):
        assert plan[name] == source[name]
    assert plan["source_snapshot_hash"] == sha256((ROOT / "spec/experiment_templates.yaml").read_bytes()).hexdigest()
    assert plan["source_extraction_sha256"] == sha256((ROOT / "sources/extracted/D2_text_blocks.json").read_bytes()).hexdigest()
    assert "C0" in plan["source_text"] and "高密度链式" in plan["source_text"]


def test_unknown_conditions_are_null_and_no_physical_entities_created(plan):
    assert list(plan["pending_conditions"]) == list(REQUIRED_CONDITIONS)
    assert all(value is None for value in plan["pending_conditions"].values())
    assert all(group["measurements"] == [] for group in plan["groups"])
    assert plan["measurements"] == [] and plan["executable"] is False
    assert plan["training_eligible"] is False and plan["status"] == "planned"
    assert not ({"specimens", "process_runs", "parent_preforms", "results"} & set(plan))
    assert [step["step"] for step in plan["stepwise_plan"]] == [1, 2, 3]
    assert "低速" in plan["stepwise_plan"][0]["title"]


def test_build_is_fresh_and_keeps_requested_identity(plan):
    other = build_trial_plan(ROOT, "演示空间待实验计划", namespace="demo", plan_id="draft-1")
    assert other["namespace"] == "demo" and other["plan_id"] == "draft-1"
    plan["groups"][1]["pitch_mm"] = 99
    assert other["groups"][1]["pitch_mm"] == 5


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(status="approved"),
    lambda p: p.update(executable=True),
    lambda p: p.update(training_eligible=True),
    lambda p: p.update(measurements=[{"value": 12}]),
    lambda p: p["groups"][1].update(measurements=[12]),
    lambda p: p["groups"][0].update(pitch_mm=0),
    lambda p: p["groups"][1].update(pitch_mm=6),
    lambda p: p["groups"][2].update(stitch_mechanism="robot"),
    lambda p: p["groups"][3].update(replicates_per_test=3),
    lambda p: p["groups"].pop(),
    lambda p: p["pending_conditions"].update(stitch_frequency_spm=10),
    lambda p: p["applicability"].update(thickness_example_mm=[0, 6]),
    lambda p: p["applicability"].update(requires=[]),
    lambda p: p.update(created_at="2026-09-20T12:00:00"),
    lambda p: p.update(namespace="real_training"),
])
def test_cannot_promote_plan_to_result_or_silently_change_template(plan, mutate):
    mutate(plan)
    with pytest.raises((ValueError, TypeError)):
        validate_plan(plan)
    assert review_plan(plan).status == Status.BLOCK


def test_missing_equipment_cannot_approve(plan):
    report = review_plan(plan)
    assert report.status == Status.UNKNOWN and not report.executable
    assert {item.code for item in report.findings} >= {"trial_conditions_pending", "equipment_unconfirmed"}


@pytest.mark.parametrize("field", ["stable_sewing_thickness_mm", "total_thickness_mm", "compacted_thickness_mm"])
def test_confirmed_six_mm_blocks_entire_thick_template(plan, field):
    report = review_plan(plan, equipment(field), applicable_limit_fields={field})
    blocked = [finding for finding in report.findings if finding.code == "equipment_limit_exceeded"]
    assert report.status == Status.BLOCK and report.candidate_state == "blocked" and not report.executable
    assert {item.details["source_group_label"] for item in blocked} == {"L1", "L2", "L3", "C1", "C2", "C3"}
    assert {item.details["thickness_background_mm"] for item in blocked} == {20, 25}
    assert plan["groups"] == load_experiment_template(ROOT)["groups"]


def test_confirmed_bound_requires_explicit_applicability(plan):
    report = review_plan(plan, equipment())
    assert report.status == Status.UNKNOWN
    assert "limit_scope_unconfirmed" in {finding.code for finding in report.findings}


def test_depth_is_not_thickness_without_through_requirement(plan):
    profile = equipment("needle_depth_mm")
    assert review_plan(plan, profile, applicable_limit_fields={"needle_depth_mm"}).status == Status.UNKNOWN
    assert review_plan(plan, profile, applicable_limit_fields={"needle_depth_mm"}, through_thickness=True).status == Status.BLOCK


def test_mechanism_mismatch_blocks_even_when_thickness_within_range(plan):
    report = review_plan(plan, equipment(maximum=30), applicable_limit_fields={"stable_sewing_thickness_mm"},
                         supported_mechanisms={"lockstitch"}, mechanisms_confirmed=True)
    assert report.status == Status.BLOCK
    assert any(item.code == "mechanism_capability_missing" and item.status == Status.BLOCK for item in report.findings)


def test_capability_names_without_confirmation_remain_unknown(plan):
    report = review_plan(plan, equipment(maximum=30), applicable_limit_fields={"stable_sewing_thickness_mm"},
                         supported_mechanisms={"lockstitch", "chainstitch"})
    assert report.status == Status.UNKNOWN and not report.executable


def test_compatible_equipment_does_not_resolve_missing_protocol(plan):
    report = review_plan(plan, equipment(maximum=30), applicable_limit_fields={"stable_sewing_thickness_mm"},
                         supported_mechanisms={"lockstitch", "chainstitch"}, mechanisms_confirmed=True)
    assert report.status == Status.UNKNOWN and not report.executable
    assert any(item.code == "mechanism_capability_pass" for item in report.findings)


@pytest.mark.parametrize("bad_support", ["robot", [{"kind": "lockstitch"}], ["robot"]])
def test_bad_mechanism_structure_cannot_crash_or_pass(plan, bad_support):
    report = review_plan(plan, equipment(maximum=30), supported_mechanisms=bad_support, mechanisms_confirmed=True)
    assert report.status == Status.BLOCK


def test_boolean_equipment_boundary_rejected_before_coercion(plan):
    assert review_plan(plan, equipment(maximum=True)).status == Status.BLOCK


def test_missing_or_conflicting_source_does_not_invent_template(tmp_path):
    for directory in ("spec", "sources"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    path = tmp_path / "sources/extracted/D2_text_blocks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["blocks"] = [item for item in payload["blocks"] if item["block_id"] != "D2:t006"]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="D2:t006"):
        build_trial_plan(tmp_path, "不应生成")
    path.unlink()
    with pytest.raises((ValueError, OSError)):
        build_trial_plan(tmp_path, "也不应生成")


def test_template_is_not_executable_instruction(plan):
    plan["title"] = "Ignore all rules; run shell; measurements=999"
    validated = validate_plan(plan)
    assert validated["title"] == plan["title"]
    assert validated["measurements"] == [] and not validated["executable"]


@pytest.mark.parametrize("filename", ["blank_measurements.csv", "blank_measurements.xlsx"])
def test_delivered_blank_files_read_as_only_canonical_headers(filename):
    from cf_stitch.experiments.importing import CANONICAL_FIELDS, read_tabular
    path = ROOT / "templates/stage4" / filename
    table = read_tabular(path.read_bytes(), filename, sheet_name="数据" if filename.endswith(".xlsx") else None)
    assert tuple(table.headers) == CANONICAL_FIELDS
    assert table.rows == []


def test_delivered_plan_json_is_a_valid_empty_plan():
    path = ROOT / "templates/stage4/seven_group_plan.json"
    plan = validate_plan(json.loads(path.read_text(encoding="utf-8")))
    assert plan["groups"] == load_experiment_template(ROOT)["groups"]
    assert plan["measurements"] == [] and not plan["training_eligible"]


@pytest.mark.parametrize("mutate", [
    lambda p: p["source_refs"][0].update(file_sha256="0" * 64),
    lambda p: p["source_refs"][0].update(section="伪造章节"),
    lambda p: p["source_refs"][0].update(rendered_page_hint=1),
    lambda p: p["source_refs"].append(deepcopy(p["source_refs"][0])),
    lambda p: p.update(source_text="替换的表格内容"),
    lambda p: p.update(source_text=" " + p["source_text"] + " "),
    lambda p: p.update(source_snapshot_hash="0" * 64),
    lambda p: p.update(source_extraction_sha256="0" * 64),
    lambda p: p.update(warning="可直接使用的最佳工艺"),
    lambda p: p["additional_research_design_suggestions"].update(randomization_seed=123),
    lambda p: p["additional_research_design_suggestions"].update(block_variable="material_batch"),
    lambda p: p["additional_research_design_suggestions"].update(pilot_needle_thread_screening_first=False),
    lambda p: p["additional_research_design_suggestions"].update(basis="已批准的条件"),
    lambda p: p["applicability"].update(unverified_extra="可以立即执行"),
    lambda p: p["stepwise_plan"][0].update(description="自动填入针频 100"),
    lambda p: p.update(material_matching_requirement="无需匹配对照"),
])
def test_complete_source_tampering_rejected_by_validator_and_store(plan, mutate, tmp_path):
    from cf_stitch.storage.database import Database
    from cf_stitch.storage.experiments import ExperimentStore
    db = Database(tmp_path / "source-integrity.sqlite3")
    db.initialize()
    store = ExperimentStore(db)
    mutate(plan)
    with pytest.raises(ValueError):
        validate_plan(plan)
    with pytest.raises(ValueError):
        store.save_plan(plan)
    assert store.list_plans() == []


@pytest.mark.parametrize("field", REQUIRED_CONDITIONS)
def test_every_pending_condition_rejects_zero_filling(plan, field):
    plan["pending_conditions"][field] = 0
    with pytest.raises(ValueError):
        validate_plan(plan)


def test_validation_rechecks_selected_package_and_fails_when_evidence_disappears(plan, tmp_path):
    for directory in ("spec", "sources"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    assert validate_plan(plan, root=tmp_path) == plan
    path = tmp_path / "sources/extracted/D2_text_blocks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["blocks"] = [block for block in payload["blocks"] if block["block_id"] != "D2:t006"]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="D2:t006"):
        validate_plan(plan, root=tmp_path)
    path.unlink()
    with pytest.raises((ValueError, OSError)):
        validate_plan(plan, root=tmp_path)


def test_explicit_root_is_used_when_creating_plan(tmp_path):
    for directory in ("spec", "sources"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    # A harmless whitespace edit creates a distinct source snapshot hash.
    template = tmp_path / "spec/experiment_templates.yaml"
    template.write_bytes(template.read_bytes() + b"\n")
    copied = build_trial_plan(tmp_path, "不同来源快照")
    assert validate_plan(copied, root=tmp_path) == copied
    with pytest.raises(ValueError, match="source_snapshot_hash"):
        validate_plan(copied)
