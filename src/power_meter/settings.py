from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal


class MeterType(StrEnum):
    STANDARD = "standard"
    TOU = "tou"


class StandardBand(StrEnum):
    UNKNOWN = "unknown"
    UP_TO_150 = "up_to_150"
    BAND_151_400 = "151_400"
    OVER_400 = "over_400"
    CUSTOM = "custom"


STANDARD_BAND_RATES: Mapping[StandardBand, float] = {
    StandardBand.UNKNOWN: 4.50,
    StandardBand.UP_TO_150: 3.38,
    StandardBand.BAND_151_400: 4.62,
    StandardBand.OVER_400: 4.84,
}

FIELD_RANGES: Mapping[str, tuple[float, float]] = {
    "custom_rate": (0.01, 100),
    "tou_peak_rate": (0.01, 100),
    "tou_offpeak_rate": (0.01, 100),
    "other_w": (0, 150),
    "extra_w": (0, 500),
    "psu_efficiency": (0.50, 0.99),
    "charge_efficiency": (0.50, 0.99),
    "cpu_tdp_w": (5, 400),
    "gpu_tdp_w": (0, 600),
    "hours_per_day": (1, 24),
}


@dataclass(frozen=True)
class Settings:
    meter_type: MeterType
    standard_band: StandardBand
    custom_rate: float
    tou_peak_rate: float
    tou_offpeak_rate: float
    extra_holidays: tuple[date, ...]
    other_w: float
    extra_w: float
    psu_efficiency: float
    charge_efficiency: float
    cpu_tdp_w: float
    gpu_tdp_w: float
    theme: Literal["dark", "light"]
    hours_per_day: int


def default_settings(is_laptop: bool) -> Settings:
    return Settings(
        meter_type=MeterType.STANDARD,
        standard_band=StandardBand.UNKNOWN,
        custom_rate=STANDARD_BAND_RATES[StandardBand.UNKNOWN],
        tou_peak_rate=6.38,
        tou_offpeak_rate=3.00,
        extra_holidays=(),
        other_w=10 if is_laptop else 25,
        extra_w=0,
        psu_efficiency=0.90 if is_laptop else 0.87,
        charge_efficiency=0.90,
        cpu_tdp_w=45 if is_laptop else 65,
        gpu_tdp_w=100 if is_laptop else 150,
        theme="dark",
        hours_per_day=5,
    )


def standard_rate(s: Settings) -> float:
    if s.standard_band is StandardBand.CUSTOM:
        return s.custom_rate
    return STANDARD_BAND_RATES[s.standard_band]


def validate_field(name: str, value: float) -> str | None:
    low, high = FIELD_RANGES[name]
    if low <= value <= high:
        return None
    return f"Must be between {low:g} and {high:g}"
