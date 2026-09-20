"""All numeric outcomes here are isolated schema test fixtures, not research data."""

import pytest
from pydantic import ValidationError

from cf_stitch.experiments.models import ObservationRow, validate_rows


def observation(**changes):
    values = dict(measurement_id="m1", source_id="fixture", source_citation="isolated software fixture",
                  provenance="experimental_measured", material_id="mat1", material_system="fixture polymer",
                  parent_preform_id="parent1", run_id="run1", specimen_id="s1", specimen_origin="fixture parent1 cut",
                  metric_name="fixture_strength", value=None, unit="MPa", measurement_stage="final_testing", missing_reason="尚未测量")
    values.update(changes)
    return values


def test_null_is_missing_not_zero():
    missing = ObservationRow(**observation())
    assert missing.value is None
    zero = ObservationRow(**observation(value=0, missing_reason=None))
    assert zero.value == 0


@pytest.mark.parametrize("changes", [
    {"value": float("nan")}, {"value": float("inf")}, {"value": True},
    {"missing_reason": None}, {"value": 1}, {"value": 1, "missing_reason": None, "unit": None},
    {"value": 1, "missing_reason": None, "unit": "未知"},
    {"value": 1, "missing_reason": None, "measurement_stage": "planned"},
    {"pitch_setting_mm": 0}, {"pitch_setting_mm": -1}, {"row_spacing_setting_mm": False},
    {"stitch_mechanism": "robot"}, {"stitch_mechanism": "herringbone"},
    {"stitch_mechanism": "lockstitch", "raw_mechanism_name": "无底线自锁"},
    {"stitch_mechanism": "unstitched", "pitch_setting_mm": 5},
    {"specimen_origin": None}, {"parent_preform_id": None}, {"run_id": None},
    {"provenance": "demo"}, {"namespace": "demo"},
    {"aggregation_level": "aggregate"}, {"reported_sample_size": 3},
    {"control_specimen_id": "s1"}, {"raw_data_sha256": "a" * 64},
    {"value": 1, "missing_reason": None, "uncertainty": .2},
    {"numerator": 1}, {"predicted_value": 4},
])
def test_invalid_semantics_are_rejected(changes):
    with pytest.raises(ValidationError):
        ObservationRow(**observation(**changes))


def test_separate_setting_and_measurement_objects():
    row = ObservationRow(**observation(metric_name="yarn_feed_tension_N", value=2, missing_reason=None,
                                      unit="N", measurement_stage="during_stitching", yarn_feed_tension_setting_N=1,
                                      upper_fabric_web_tension_setting_N=20, lower_fabric_web_tension_setting_N=30,
                                      pitch_setting_mm=5, row_spacing_setting_mm=10, mechanical_needle_spacing_setting_mm=120,
                                      needle_depth_setting_mm=25, foot_lift_setting_mm=30, compacted_thickness_mm=22,
                                      stitch_frequency_setting_spm=60, line_speed_setting_m_min=.3))
    assert row.value == 2 and row.yarn_feed_tension_setting_N == 1
    assert row.mechanical_needle_spacing_setting_mm == 120
    assert row.compacted_thickness_mm == 22 and row.needle_depth_setting_mm == 25


def test_custom_mechanism_path_platform_remain_separate():
    row = ObservationRow(**observation(stitch_mechanism="custom_unconfirmed", raw_mechanism_name="无底线自锁", path_pattern="herringbone", motion_platform="robot"))
    assert row.stitch_mechanism.value == "custom_unconfirmed"


def test_literature_aggregate_has_no_fake_physical_entities():
    row = ObservationRow(**observation(provenance="literature_measured", literature_record_id="Table 4 group A mean",
                                      aggregation_level="aggregate", reported_sample_size=5, specimen_id=None,
                                      parent_preform_id=None, run_id=None))
    assert row.reported_sample_size == 5 and row.specimen_id is None
    with pytest.raises(ValidationError, match="文献均值"):
        ObservationRow(**dict(row.model_dump(), specimen_id="fake-copy"))


@pytest.mark.parametrize("changes", [
    {"numerator": None}, {"denominator": 0}, {"observation_window": None},
    {"numerator": .5}, {"denominator": 2.5}, {"value": 99}, {"unit": "MPa"},
])
def test_count_rate_requires_consistent_counts(changes):
    values = observation(value=50, missing_reason=None, metric_kind="count_rate", unit="%", numerator=1, denominator=2, observation_window="fixture 10 min")
    values.update(changes)
    with pytest.raises(ValidationError):
        ObservationRow(**values)


def test_count_rate_and_missing_context_report():
    row = observation(value=.5, missing_reason=None, metric_kind="count_rate", unit="1", numerator=1, denominator=2, observation_window="fixture 10 min")
    result = validate_rows([row])
    assert result["valid"] and result["warnings"]
    assert result["rows"][0]["value"] == .5
    assert not validate_rows([])["valid"]
    assert not validate_rows([row], "demo")["valid"]


@pytest.mark.parametrize("provenance", ["experimental_measured", "literature_measured", "simulation", "demo", "prediction"])
def test_provenance_is_explicit(provenance):
    row = observation(provenance=provenance, namespace="demo" if provenance == "demo" else "real")
    if provenance == "literature_measured":
        row["literature_record_id"] = "table 1 individual 1"
    assert ObservationRow(**row).provenance == provenance
