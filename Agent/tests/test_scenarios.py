"""Source-preserving scenario cards are not equipment capabilities or task defaults."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import get_args

import pytest

from cf_stitch.domain.schemas import Scenario
from cf_stitch.knowledge.documents import load_extracted_document
from cf_stitch.knowledge.seeds import load_parameter_seed
from cf_stitch.scenarios import SCENARIO_LABELS, ScenarioEvidenceError, get_scenario


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cards():
    return {key: get_scenario(ROOT, key) for key in SCENARIO_LABELS}


@pytest.fixture
def source_copy(tmp_path):
    for directory in ("sources", "spec"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    return tmp_path


def by_id(card):
    return {claim["record_id"]: claim for claim in card.claims}


def test_scenario_keys_match_research_task_and_unknown_is_not_general_fallback(cards):
    assert set(cards) == set(get_args(Scenario))
    with pytest.raises(ValueError, match="未知场景"):
        get_scenario(ROOT, "robot")


@pytest.mark.parametrize("key", list(SCENARIO_LABELS))
def test_every_claim_has_exact_hash_and_original_block_evidence(cards, key):
    originals = {doc: load_extracted_document(ROOT, doc) for doc in ("D1", "D2")}
    for claim in cards[key].claims:
        assert claim["enforcement"] == "advisory"
        assert claim["verification_status"] == "source_only"
        assert claim["source_refs"]
        for ref, evidence in zip(claim["source_refs"], claim["evidence_blocks"], strict=True):
            original = originals[ref["document_id"]]
            block = next(item for item in original.blocks if item.block_id == ref["block_id"])
            assert evidence["text"] == block.text
            assert evidence["file_sha256"] == ref["file_sha256"] == original.sha256
            assert evidence["extraction_sha256"] == original.extraction_sha256
            assert evidence["hash_basis"] == "declared_original_hash"
            assert evidence["identity_status"] == "declared_only"
            assert evidence["images_parsed"] is False
            for field in ("paragraph_index", "table_index", "row_index", "section", "section_basis"):
                assert evidence[field] == getattr(block, field)


def test_all_original_33_seed_claims_are_unchanged_and_scope_isolated(cards):
    originals = {item["record_id"]: item for item in load_parameter_seed(ROOT)["records"]}
    found = set()
    for key, card in cards.items():
        for claim in card.claims:
            if claim["record_id"] in originals:
                original = originals[claim["record_id"]]
                assert all(claim[field] == value for field, value in original.items())
                found.add(claim["record_id"])
            if key != "general_research":
                assert claim["scope"] == key
    assert found == originals.keys()
    assert "D2-P" not in by_id(cards["dual_layer_fixation"])
    assert "D2-F" not in by_id(cards["j_beam"])


def test_v_splice_keeps_pitch_swing_depth_lift_and_two_errors_separate(cards):
    claims = by_id(cards["v_splice"])
    for record_id, bounds in (("D2-V-P", (3, 10)), ("D1-V-SWING", (5, 15))):
        assert (claims[record_id]["min"], claims[record_id]["max"]) == bounds
    assert claims["D1-V-FMAX"]["max"] == 800
    assert claims["D1-V-DEPTH"]["max"] == 6
    assert claims["D1-V-LIFT"]["max"] == 20
    assert claims["D1-V-LIFT"]["semantic_status"] == "confirm_component_name"
    assert claims["D1-V-REPEAT"]["field"] == "repeatability_abs_mm"
    assert claims["D1-V-REPEAT"]["max"] == 0.05
    assert claims["D1-V-ERR"]["field"] == "stitch_error_abs_mm"
    assert claims["D1-V-ERR"]["max"] == 0.3
    assert claims["D1-V-STRENGTH"]["evidence_kind"] == "design_target"
    assert claims["D1-V-STRENGTH"]["min"] == 70
    assert claims["D1-V-WIDTH"]["min"] == 600


def test_j_station_platforms_do_not_classify_unconfirmed_mechanism(cards):
    card = cards["j_beam"]
    assert [(item["station"], item["motion_platform"]) for item in card.arrangements] == [
        (1, "fixed_head"), (2, "fixed_head"), (3, "robot"),
    ]
    assert all(item["stitch_mechanism"] == "custom_unconfirmed" for item in card.arrangements)
    assert all(item["raw_mechanism_name"] == "小型无底线自锁式缝合头" for item in card.arrangements)
    assert all(item["source_refs"][0]["block_id"] == "D1:p0297" for item in card.arrangements)
    assert {"R_radius_mm", "needle_normal_angle_deg", "TCP_calibration_reference", "fixture_envelope"} <= set(card.context_fields)


def test_j_density_conditional_derivation_remains_pending(cards):
    claims = by_id(cards["j_beam"])
    density = claims["D1-J-DENSITY"]
    assert (density["min"], density["max"], density["unit"]) == (5, 15, "stitches/cm")
    assert density["semantic_status"] == "pending_review"
    assert "均匀单排" in density["derivation"]
    assert density["derived_pitch_range_mm"] == pytest.approx([2 / 3, 2])
    assert claims["D1-J-FMAX"]["max"] == 10
    assert claims["D1-J-FMAX"]["unit"] == "Hz"
    assert "one stitch per cycle" in claims["D1-J-FMAX"]["conversion"]
    assert claims["D1-J-DEPTH"]["max"] == 30
    assert claims["D1-J-DIMENSIONS"]["dimensions"] == [240, 100, 65]
    assert claims["D1-J-MASS"]["approximate"] is True
    assert any("待审核" in note for note in cards["j_beam"].notes)


def test_dual_layer_has_own_window_and_separate_tension_objects(cards):
    card = cards["dual_layer_fixation"]
    claims = by_id(card)
    assert (claims["D1-DUAL-P"]["min"], claims["D1-DUAL-P"]["max"]) == (10, 30)
    assert (claims["D1-DUAL-F"]["min"], claims["D1-DUAL-F"]["max"]) == (100, 500)
    assert (claims["D1-DUAL-T"]["min"], claims["D1-DUAL-T"]["max"]) == (20, 80)
    assert {item["field"] for item in card.parameter_bindings} == {"upper_fabric_web_tension_N", "lower_fabric_web_tension_N"}
    assert all(item["source_record_id"] == "D1-DUAL-T" and item["value"] is None for item in card.parameter_bindings)
    assert "fabric_web_tension_difference_N" in card.context_fields
    assert "line_speed_difference_m_min" in card.context_fields
    assert claims["D1-DUAL-ALIGN"]["evidence_kind"] == "design_target"
    assert claims["D1-DUAL-ALIGN"]["max"] == 0.5


def test_dual_layer_conflicts_are_neither_merged_nor_selected(cards):
    claims = by_id(cards["dual_layer_fixation"])
    assert [claims[key]["max"] for key in ("D1-DUAL-H5", "D1-DUAL-H6")] == [5, 6]
    assert claims["D1-DUAL-SPACING"]["examples"] == [100, 120, 200]
    assert claims["D1-DUAL-SPACING"]["field"] == "mechanical_needle_spacing_mm"
    assert claims["D1-DUAL-SPACE1200"]["value"] == 1200
    assert claims["D1-DUAL-SPACE1500-2000"]["min"] == 1500
    assert claims["D1-DUAL-SPACE1500-2000"]["max"] == 2000
    assert claims["D1-DUAL-WIDTH"]["max"] == 1000
    assert all(claims[key]["semantic_status"] == "pending_actual_mechanism" for key in (
        "D1-DUAL-CHAIN", "D1-DUAL-TWO-THREAD", "D1-DUAL-ROTARY-HOOK",
    ))
    assert len(cards["dual_layer_fixation"].pending) == 4


@pytest.mark.parametrize("key", list(SCENARIO_LABELS))
def test_no_default_task_values_or_approval_or_collision_claim(cards, key):
    card = cards[key]
    assert card.context_schema
    assert all(item["default"] is None and item["missing_reason"] for item in card.context_schema)
    assert card.reachability_status == card.collision_status == "UNKNOWN"
    assert card.candidate_approval is False
    assert card.to_dict()["context_fields"] == card.context_fields


def test_cards_and_serializations_do_not_mutate_sources_or_subsequent_calls():
    paths = [ROOT / "spec/domain_parameters.yaml", ROOT / "sources/manifest.json", *sorted((ROOT / "sources/extracted").glob("*.json"))]
    before = {path: sha256(path.read_bytes()).hexdigest() for path in paths}
    first = get_scenario(ROOT, "dual_layer_fixation")
    snapshot = deepcopy(first.claims)
    serialized = first.to_dict()
    serialized["claims"][0]["min"] = -999
    assert first.claims == snapshot
    first.claims[0]["min"] = -999
    first.claims[0]["source_refs"][0]["file_sha256"] = "0" * 64
    assert get_scenario(ROOT, "dual_layer_fixation").claims == snapshot
    assert {path: sha256(path.read_bytes()).hexdigest() for path in paths} == before


@pytest.mark.parametrize("relative", ["spec/domain_parameters.yaml", "sources/manifest.json", "sources/extracted/D1_text_blocks.json"])
def test_missing_source_does_not_return_a_default_card(source_copy, relative):
    (source_copy / relative).unlink()
    with pytest.raises(ScenarioEvidenceError, match="来源不可用"):
        get_scenario(source_copy, "dual_layer_fixation")


def test_extra_design_claim_requires_actual_quoted_text(source_copy):
    path = source_copy / "sources/extracted/D1_text_blocks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    next(block for block in payload["blocks"] if block["block_id"] == "D1:p0347")["text"] = "外形尺寸尚未给出"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ScenarioEvidenceError, match="缺少可定位原文"):
        get_scenario(source_copy, "j_beam")


def test_missing_block_anchor_fails_without_borrowing_other_version(source_copy):
    path = source_copy / "sources/extracted/D1_text_blocks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["blocks"] = [block for block in payload["blocks"] if block["block_id"] != "D1:p0253"]
    payload["metadata"]["block_count"] = len(payload["blocks"])
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ScenarioEvidenceError):
        get_scenario(source_copy, "v_splice")
