import math
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any, Final, Literal

from power_meter.settings import FIELD_RANGES, MeterType, Settings, StandardBand, validate_field

RATE_FIELDS: Final = ("custom_rate", "tou_peak_rate", "tou_offpeak_rate")
HARDWARE_FIELDS: Final = ("other_w", "extra_w", "psu_efficiency", "cpu_tdp_w", "gpu_tdp_w")
PERCENT_FIELDS: Final = frozenset({"psu_efficiency"})


def parse_number(raw: str) -> float | str:
    """Return the number typed in `raw`, or a short message saying what is wrong with it."""
    text = raw.strip()
    if not text:
        return "Required"
    if "," in text:
        return "Use a dot for decimals, e.g. 4.50"
    try:
        value = float(text)
    except ValueError:
        return "Enter a number"
    return value if math.isfinite(value) else "Enter a number"


def active_fields(meter_type: MeterType, band: StandardBand) -> tuple[str, ...]:
    """The number fields that apply to this choice of meter; the rest are left untouched."""
    if meter_type is MeterType.TOU:
        rates: tuple[str, ...] = ("tou_peak_rate", "tou_offpeak_rate")
    elif band is StandardBand.CUSTOM:
        rates = ("custom_rate",)
    else:
        rates = ()
    return rates + HARDWARE_FIELDS


def form_from_settings(s: Settings) -> dict[str, str]:
    form = {
        "meter_type": s.meter_type.value,
        "standard_band": s.standard_band.value,
        "theme": s.theme,
    }
    for name in RATE_FIELDS:
        form[name] = _format(getattr(s, name), decimals=2)
    for name in HARDWARE_FIELDS:
        value: float = getattr(s, name)
        form[name] = _as_percent(value) if name in PERCENT_FIELDS else _format(value, decimals=0)
    return form


def settings_from_form(
    base: Settings, raw: Mapping[str, str]
) -> tuple[Settings | None, dict[str, str]]:
    """Read the form back into Settings. Errors are keyed by field; the Settings is returned only
    when there are none. Fields the form doesn't show, and fields that don't apply to the chosen
    meter, keep their values from `base`."""
    meter_type = MeterType(raw["meter_type"])
    band = StandardBand(raw["standard_band"])
    theme: Literal["dark", "light"] = "light" if raw["theme"] == "light" else "dark"

    # Field names are only known at run time, so replace() can't type-check them.
    numbers: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name in active_fields(meter_type, band):
        result = _field_value(name, raw[name])
        if isinstance(result, str):
            errors[name] = result
        else:
            numbers[name] = result
    if errors:
        return None, errors

    settings = replace(base, meter_type=meter_type, standard_band=band, theme=theme)
    return replace(settings, **numbers), {}


def _field_value(name: str, raw: str) -> float | str:
    parsed = parse_number(raw)
    if isinstance(parsed, str):
        return parsed
    if name in PERCENT_FIELDS:
        fraction = float(Decimal(raw.strip()) / 100)
        if validate_field(name, fraction) is None:
            return fraction
        low, high = FIELD_RANGES[name]
        return f"Must be between {low * 100:g} and {high * 100:g}"
    return validate_field(name, parsed) or parsed


# Percents are scaled in decimal: in binary floating point 0.533 * 100 and 53.3 / 100 both pick up
# noise, so the stored fraction would drift on every save.
def _as_percent(fraction: float) -> str:
    return format((Decimal(repr(fraction)) * 100).normalize(), "f")


def _format(value: float, decimals: int) -> str:
    text = f"{value:.{decimals}f}"
    # A value set outside the form (an edited config.json) may carry more decimals than the form
    # normally shows; keep all of them so that saving doesn't silently change it.
    return text if float(text) == value else repr(value)
