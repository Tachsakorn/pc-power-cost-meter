from dataclasses import replace
from datetime import date

import pytest

from power_meter.settings import MeterType, Settings, StandardBand, default_settings
from power_meter.ui.form import form_from_settings, parse_number, settings_from_form

DESKTOP = default_settings(is_laptop=False)


def edited(base: Settings, **changes: str) -> dict[str, str]:
    return form_from_settings(base) | changes


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" 4.5 ", 4.5),
        ("4", 4.0),
        ("4,5", "Use a dot for decimals, e.g. 4.50"),
        ("abc", "Enter a number"),
        ("", "Required"),
        ("   ", "Required"),
        ("nan", "Enter a number"),
        ("inf", "Enter a number"),
    ],
)
def test_rate_field_parsing(raw: str, expected: float | str) -> None:
    assert parse_number(raw) == expected


def test_percent_shown_without_float_noise() -> None:
    assert form_from_settings(replace(DESKTOP, psu_efficiency=0.533))["psu_efficiency"] == "53.3"
    assert form_from_settings(replace(DESKTOP, psu_efficiency=0.9))["psu_efficiency"] == "90"


def test_percent_converted_to_fraction() -> None:
    settings, errors = settings_from_form(DESKTOP, edited(DESKTOP, psu_efficiency="53.3"))
    assert errors == {}
    assert settings is not None
    assert settings.psu_efficiency == 0.533


def test_percent_shown_as_whole_number() -> None:
    assert form_from_settings(DESKTOP)["psu_efficiency"] == "87"


def test_typed_percent_gives_exact_fraction() -> None:
    for tenths in range(500, 991):
        typed = f"{tenths // 10}.{tenths % 10}"
        settings, _ = settings_from_form(DESKTOP, edited(DESKTOP, psu_efficiency=typed))
        assert settings is not None
        assert settings.psu_efficiency == float(f"0.{tenths:03d}"), typed


def test_stored_percent_survives_round_trip() -> None:
    for tenths in range(500, 991):
        settings = replace(DESKTOP, psu_efficiency=float(f"0.{tenths:03d}"))
        assert settings_from_form(settings, form_from_settings(settings)) == (settings, {})


def test_percent_range_error_speaks_in_percent() -> None:
    settings, errors = settings_from_form(DESKTOP, edited(DESKTOP, psu_efficiency="0.9"))
    assert settings is None
    assert errors == {"psu_efficiency": "Must be between 50 and 99"}


def test_out_of_range_reported_per_field() -> None:
    form = edited(DESKTOP, other_w="200", cpu_tdp_w="1", gpu_tdp_w="4,5")
    settings, errors = settings_from_form(DESKTOP, form)
    assert settings is None
    assert errors == {
        "other_w": "Must be between 0 and 150",
        "cpu_tdp_w": "Must be between 5 and 400",
        "gpu_tdp_w": "Use a dot for decimals, e.g. 4.50",
    }


@pytest.mark.parametrize(
    "settings",
    [
        DESKTOP,
        default_settings(is_laptop=True),
        replace(DESKTOP, standard_band=StandardBand.CUSTOM, custom_rate=4.1234),
        replace(DESKTOP, meter_type=MeterType.TOU, tou_peak_rate=5.7982, tou_offpeak_rate=2.6369),
        replace(DESKTOP, psu_efficiency=0.875, other_w=32.5, theme="light"),
        replace(DESKTOP, psu_efficiency=0.533),
        replace(DESKTOP, psu_efficiency=0.5333333333333333, other_w=12.345678901234567),
    ],
)
def test_round_trip_form(settings: Settings) -> None:
    assert settings_from_form(settings, form_from_settings(settings)) == (settings, {})


def test_rates_shown_with_two_decimals() -> None:
    form = form_from_settings(DESKTOP)
    assert (form["custom_rate"], form["tou_peak_rate"], form["tou_offpeak_rate"]) == (
        "4.50",
        "6.38",
        "3.00",
    )


def test_tou_fields_ignored_when_standard() -> None:
    form = edited(DESKTOP, meter_type="standard", tou_peak_rate="abc", tou_offpeak_rate="")
    settings, errors = settings_from_form(DESKTOP, form)
    assert errors == {}
    assert settings == DESKTOP


def test_custom_rate_ignored_unless_custom_band() -> None:
    for meter_type in ("standard", "tou"):
        form = edited(DESKTOP, meter_type=meter_type, custom_rate="")
        assert settings_from_form(DESKTOP, form)[1] == {}


def test_custom_rate_validated_for_custom_band() -> None:
    form = edited(DESKTOP, standard_band="custom", custom_rate="0")
    assert settings_from_form(DESKTOP, form) == (
        None,
        {"custom_rate": "Must be between 0.01 and 100"},
    )


def test_tou_rates_validated_when_tou() -> None:
    form = edited(DESKTOP, meter_type="tou", tou_peak_rate="", tou_offpeak_rate="3.2")
    assert settings_from_form(DESKTOP, form) == (None, {"tou_peak_rate": "Required"})


def test_choices_applied() -> None:
    form = edited(DESKTOP, meter_type="tou", standard_band="over_400", theme="light")
    settings, _ = settings_from_form(DESKTOP, form)
    assert settings is not None
    assert settings.meter_type is MeterType.TOU
    assert settings.standard_band is StandardBand.OVER_400
    assert settings.theme == "light"


def test_fields_not_on_the_form_come_from_base() -> None:
    base = replace(
        DESKTOP, hours_per_day=9, charge_efficiency=0.8, extra_holidays=(date(2026, 12, 31),)
    )
    settings, _ = settings_from_form(base, edited(base, other_w="30"))
    assert settings == replace(base, other_w=30)
