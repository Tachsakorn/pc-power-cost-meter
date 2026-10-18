from dataclasses import replace

import pytest

from power_meter.settings import (
    MeterType,
    StandardBand,
    default_settings,
    standard_rate,
    validate_field,
)


def test_desktop_defaults() -> None:
    s = default_settings(is_laptop=False)
    assert s.other_w == 25
    assert s.psu_efficiency == 0.87
    assert s.cpu_tdp_w == 65
    assert s.gpu_tdp_w == 150
    assert s.meter_type is MeterType.STANDARD
    assert s.standard_band is StandardBand.UNKNOWN
    assert s.theme == "dark"
    assert s.hours_per_day == 5


def test_animate_scene_defaults_on() -> None:
    assert default_settings(is_laptop=False).animate_scene is True
    assert default_settings(is_laptop=True).animate_scene is True


def test_laptop_defaults() -> None:
    s = default_settings(is_laptop=True)
    assert s.other_w == 10
    assert s.psu_efficiency == 0.90
    assert s.cpu_tdp_w == 45
    assert s.gpu_tdp_w == 100


def test_shared_defaults() -> None:
    s = default_settings(is_laptop=False)
    assert s.charge_efficiency == 0.90
    assert s.extra_w == 0
    assert s.extra_holidays == ()
    assert (s.tou_peak_rate, s.tou_offpeak_rate) == (6.38, 3.00)


@pytest.mark.parametrize(
    ("band", "rate"),
    [
        (StandardBand.UNKNOWN, 4.50),
        (StandardBand.UP_TO_200, 3.38),
        (StandardBand.BAND_201_400, 4.62),
        (StandardBand.OVER_400, 4.84),
    ],
)
def test_standard_rate_bands(band: StandardBand, rate: float) -> None:
    s = default_settings(is_laptop=False)
    assert standard_rate(replace(s, standard_band=band)) == rate


def test_standard_rate_custom_uses_custom_rate() -> None:
    s = replace(default_settings(is_laptop=False), standard_band=StandardBand.CUSTOM)
    assert standard_rate(s) == s.custom_rate


@pytest.mark.parametrize("value", [0.5, 0.99, 0.75])
def test_validate_field_accepts_values_in_range(value: float) -> None:
    assert validate_field("psu_efficiency", value) is None


@pytest.mark.parametrize("value", [0.49, 1.0])
def test_validate_field_rejects_values_out_of_range(value: float) -> None:
    assert validate_field("psu_efficiency", value) is not None


def test_validate_field_message_names_the_range() -> None:
    assert validate_field("custom_rate", 0) == "Must be between 0.01 and 100"
    assert validate_field("charge_efficiency", 2) == "Must be between 0.5 and 0.99"


def test_validate_field_unknown_name_raises() -> None:
    with pytest.raises(KeyError):
        validate_field("nope", 1)


@pytest.mark.parametrize("value", [2.5, 0.5, 23.99])
def test_validate_field_rejects_fractional_hours(value: float) -> None:
    assert validate_field("hours_per_day", value) == "Must be a whole number"


@pytest.mark.parametrize("value", [1, 5.0, 24])
def test_validate_field_accepts_whole_hours(value: float) -> None:
    assert validate_field("hours_per_day", value) is None


@pytest.mark.parametrize("value", [0, 25])
def test_validate_field_still_range_checks_hours(value: float) -> None:
    assert validate_field("hours_per_day", value) == "Must be between 1 and 24"


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_validate_field_rejects_non_finite_hours(value: float) -> None:
    assert validate_field("hours_per_day", value) == "Must be a whole number"
