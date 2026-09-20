"""Stage-three theoretical checks use fixtures, never experimental labels."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from cf_stitch.rules.calculations import (
    convert_unit, ideal_line_speed, j_density_to_pitch, matched_change,
    rectangular_density,
)
from cf_stitch.rules.results import Status


ROOT = Path(__file__).resolve().parents[1]
ARRAY = {"regular_rectangular": True, "one_puncture_per_cell": True}
FEED = {"straight_synchronized": True, "pitch_is_feed_advance": True,
        "one_stitch_per_cycle": True}


@pytest.mark.parametrize("p,s,expected", [(5, 5, 40000), (5, 10, 20000),
                                           (10, 10, 10000), (10, 20, 5000)])
def test_required_four_theoretical_densities(p, s, expected):
    result = rectangular_density(p, s, **ARRAY)
    assert result.status == Status.PASS
    assert result.value == expected
    assert result.unit == "points/m^2"
    assert result.provenance == "formula_calculated"
    assert result.assumptions
    assert result.details["equipment_approval"] is False
    assert result.details["is_measurement"] is False


@pytest.mark.parametrize("p,s", [(0, 5), (5, 0), (-1, 5), (True, 5),
                                   (5, float("nan")), (float("inf"), 5), ("5", 5)])
def test_density_invalid_math_is_blocked(p, s):
    result = rectangular_density(p, s, **ARRAY)
    assert result.status == Status.BLOCK
    assert result.value is None
    json.loads(result.model_dump_json())


def test_unstitched_is_explicit_zero_and_never_computes_division():
    result = rectangular_density(None, None, mechanism="unstitched")
    assert result.status == Status.PASS and result.value == 0
    assert "不进行除法" in result.assumptions[0]
    for p, s in ((0, 0), (5, None), (None, 5)):
        assert rectangular_density(p, s, mechanism="unstitched").status == Status.BLOCK


@pytest.mark.parametrize("kwargs", [
    {}, {"regular_rectangular": True}, {"one_puncture_per_cell": True},
    {**ARRAY, "regular_rectangular": "True"},
    {**ARRAY, "one_puncture_per_cell": 1},
    {**ARRAY, "extra_punctures": None}, {**ARRAY, "extra_punctures": True},
    {**ARRAY, "mechanism": "double_needle"},
    {**ARRAY, "mechanism": "custom_unconfirmed"},
    {**ARRAY, "mechanism": "robot"},
    {**ARRAY, "path_pattern": "herringbone"},
    {**ARRAY, "path_pattern": "return_stitches"},
])
def test_density_does_not_apply_simple_formula_without_supported_assumptions(kwargs):
    result = rectangular_density(5, 5, **kwargs)
    assert result.status == Status.UNKNOWN and result.value is None


def test_missing_density_is_not_zero_and_window_is_not_math_constraint():
    assert rectangular_density(None, 5, **ARRAY).status == Status.UNKNOWN
    # Dual-layer p=20 is a valid spacing although outside the D2 generic window.
    assert rectangular_density(20, 5, **ARRAY).value == 10000


@pytest.mark.parametrize("value,source,target,expected", [
    (2, "cm", "mm", 20), (2000, "μm", "mm", 2),
    (2, "m", "mm", 2000), (100, "cN", "N", 1),
    (1000, "mN", "N", 1), (1, "gf", "N", 0.00980665),
    (100, "stitches/min", "stitches/s", 100 / 60),
    (2, "针/s", "针/min", 120), (10, "mm/s", "m/min", 0.6),
    (0.5, "m/min", "mm/min", 500), (70, "%", "1", 0.7),
    (10, "stitches/cm", "stitches/mm", 1),
])
def test_unit_conversions_preserve_original_inputs(value, source, target, expected):
    result = convert_unit(value, source, target)
    assert result.status == Status.PASS
    assert result.value == pytest.approx(expected)
    assert result.details["inputs"]["value"] == value
    assert result.details["inputs"]["from_unit"] == source
    assert result.unit == target and result.basis


def test_hertz_requires_explicit_cycle_relationship():
    assert convert_unit(10, "Hz", "stitches/min").status == Status.UNKNOWN
    result = convert_unit(10, "Hz", "stitches/min", stitches_per_cycle=1)
    assert result.status == Status.PASS and result.value == 600
    assert "每周期 1 针" in result.assumptions[0]
    assert convert_unit(600, "stitches/min", "Hz", stitches_per_cycle=1).value == 10
    assert convert_unit(10, "Hz", "stitches/min", stitches_per_cycle=2).value == 1200
    assert convert_unit(10, "Hz", "stitches/s", stitches_per_cycle=1).value == 10


@pytest.mark.parametrize("count", [0, -1, True, float("nan"), float("inf"), "1"])
def test_invalid_stitches_per_cycle_is_blocked(count):
    result = convert_unit(10, "Hz", "stitches/min", stitches_per_cycle=count)
    assert result.status == Status.BLOCK and result.value is None


@pytest.mark.parametrize("source,target", [("stitches/min", "m/min"),
                                          ("mm", "N"), ("mm", "stitches/cm")])
def test_dimension_mismatch_cannot_be_reinterpreted(source, target):
    result = convert_unit(10, source, target)
    assert result.status == Status.BLOCK and result.value is None


def test_unknown_unit_not_accepted_even_if_same_spelling():
    assert convert_unit(10, "unverified", "unverified").status == Status.UNKNOWN
    assert convert_unit(None, "mm", "cm").status == Status.UNKNOWN
    assert convert_unit(True, "mm", "cm").status == Status.BLOCK


def test_ideal_speed_acceptance_and_clear_provenance():
    result = ideal_line_speed(5, 100, **FEED)
    assert result.status == Status.PASS and result.value == 0.5
    assert result.unit == "m/min" and result.provenance == "formula_calculated"
    assert result.source_refs == []  # Project kinematic derivation, not a D2 claim.
    assert result.basis == ["PROJECT_SPEC.md §6 数值计算"]


@pytest.mark.parametrize("missing", list(FEED))
def test_ideal_feed_assumptions_cannot_be_implicit(missing):
    flags = {**FEED, missing: False}
    result = ideal_line_speed(5, 100, **flags)
    assert result.status == Status.UNKNOWN and result.value is None


def test_line_speed_invalid_and_missing_values():
    assert ideal_line_speed(None, 100, **FEED).status == Status.UNKNOWN
    assert ideal_line_speed(0, 100, **FEED).status == Status.BLOCK
    assert ideal_line_speed(5, float("inf"), **FEED).status == Status.BLOCK


@pytest.mark.parametrize("p,s", [(1e-200, 1e-200), (1e200, 1e200)])
def test_density_extreme_values_do_not_create_infinity_or_false_zero(p, s):
    result = rectangular_density(p, s, **ARRAY)
    assert result.status == Status.BLOCK and result.value is None


def test_intermediate_overflow_is_avoided_when_final_value_is_representable():
    result = rectangular_density(1e200, 1e110, **ARRAY)
    assert result.status == Status.PASS and result.value == 1e-304
    result = ideal_line_speed(1e200, 1e110, **FEED)
    assert result.status == Status.PASS and result.value == 1e307
    result = matched_change(1e308, -1e308, matched_control=True)
    assert result.value == -200  # Naive subtraction would overflow first.
    assert result.status == Status.WARN  # Signed baseline needs interpretation.


@pytest.mark.parametrize("result", [
    convert_unit(1e308, "m", "mm"),
    convert_unit(5e-324, "mm", "m"),
    ideal_line_speed(1e308, 1e308, **FEED),
    ideal_line_speed(1e-300, 1e-300, **FEED),
    matched_change(1e308, 1e-308, matched_control=True),
    j_density_to_pitch(5e-324, 1, uniform_single_row=True),
])
def test_nonrepresentable_outputs_are_blocked_without_numbers(result):
    assert result.status == Status.BLOCK and result.value is None
    json.loads(result.model_dump_json())


def test_matched_gain_loss_use_actual_values_and_retain_negative_loss():
    gain = matched_change(120, 100, matched_control=True, unit="MPa")
    loss = matched_change(120, 100, matched_control=True, mode="loss", unit="MPa")
    assert gain.value == 20 and loss.value == -20
    assert "未截断" in loss.message
    assert loss.details["inputs"]["original_unit"] == "MPa"
    assert gain.provenance == "formula_calculated"


@pytest.mark.parametrize("kwargs,status", [
    ({"stitched_mean": 120, "control_mean": 100}, Status.UNKNOWN),
    ({"stitched_mean": 120, "control_mean": 100, "matched_control": "true"}, Status.UNKNOWN),
    ({"stitched_mean": None, "control_mean": 100, "matched_control": True}, Status.UNKNOWN),
    ({"stitched_mean": 120, "control_mean": 0, "matched_control": True}, Status.BLOCK),
    ({"stitched_mean": True, "control_mean": 100, "matched_control": True}, Status.BLOCK),
    ({"stitched_mean": float("nan"), "control_mean": 100, "matched_control": True}, Status.BLOCK),
    ({"stitched_mean": 120, "control_mean": 100, "matched_control": True, "mode": "unknown"}, Status.BLOCK),
])
def test_missing_unmatched_zero_or_invalid_control_cannot_produce_change(kwargs, status):
    result = matched_change(**kwargs)
    assert result.status == status and result.value is None


def test_j_density_requires_assumption_and_stays_pending():
    assert j_density_to_pitch().status == Status.UNKNOWN
    result = j_density_to_pitch(uniform_single_row=True)
    assert result.status == Status.WARN
    assert result.value == pytest.approx([2 / 3, 2])
    assert result.details["review_status"] == "pending"
    assert result.details["use_as_default"] is False
    assert result.details["source_range_stitches_cm"] == [5, 15]
    assert "旧提取 JSON" in result.details["source_identity_note"]
    supplied = j_density_to_pitch(10, 20, uniform_single_row=True)
    assert supplied.value == [0.5, 1]
    assert "不是 D1" in supplied.details["input_scope"]


@pytest.mark.parametrize("lower,upper", [(15, 5), (0, 15), (True, 15),
                                         (5, float("inf")), (float("nan"), 15)])
def test_j_density_rejects_invalid_bounds(lower, upper):
    result = j_density_to_pitch(lower, upper, uniform_single_row=True)
    assert result.status == Status.BLOCK and result.value is None


def test_real_formula_and_j_citations_exist_in_declared_snapshot():
    for result in (rectangular_density(5, 5, **ARRAY), j_density_to_pitch(uniform_single_row=True)):
        for ref in result.source_refs:
            document = json.loads((ROOT / "sources" / "extracted" / f"{ref.document_id}_text_blocks.json").read_text(encoding="utf-8"))
            assert document["metadata"]["sha256"] == ref.file_sha256
            block = next(block for block in document["blocks"] if block["block_id"] == ref.block_id)
            assert ref.excerpt in block["text"]


def test_document_instructions_are_not_numeric_expressions():
    payload = "__import__('os').system('not-a-real-command')"
    result = rectangular_density(payload, 5, **ARRAY)
    assert result.status == Status.BLOCK
    assert result.details["inputs"]["pitch_mm"] == payload
    assert convert_unit(payload, "mm", "cm").status == Status.BLOCK


def test_decimal_underflow_input_is_not_treated_as_zero():
    result = rectangular_density(Decimal("1e-1000000"), 5, **ARRAY)
    assert result.status == Status.BLOCK and result.value is None


def test_returned_reference_mutation_does_not_change_later_results():
    first = rectangular_density(5, 5, **ARRAY)
    first.source_refs[0].section = "调用方修改"
    second = rectangular_density(5, 5, **ARRAY)
    assert second.source_refs[0].section == "五、缝合密度与结构设计"
