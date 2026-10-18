import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from importlib import resources
from typing import Protocol

from power_meter.settings import MeterType, Settings, standard_rate

PEAK_START = time(9)
PEAK_END = time(22)
LOOKAHEAD_DAYS = 14


class Period(StrEnum):
    FLAT = "flat"
    PEAK = "peak"
    OFF_PEAK = "off_peak"


@dataclass(frozen=True)
class RateInfo:
    rate: float
    period: Period
    next_change: datetime | None


class HolidayCalendar:
    def __init__(self, dates: Iterable[date]) -> None:
        self._dates = frozenset(dates)

    @property
    def dates(self) -> frozenset[date]:
        return self._dates

    def is_holiday(self, d: date) -> bool:
        return d in self._dates

    @classmethod
    def bundled(cls, extra: Iterable[date] = ()) -> "HolidayCalendar":
        """Thai holidays billed off-peak all day, as shipped with the app."""
        text = resources.files("power_meter").joinpath("data/holidays_th.json").read_text("utf-8")
        years: dict[str, list[str]] = json.loads(text)["years"]
        shipped = (date.fromisoformat(day) for days in years.values() for day in days)
        return cls([*shipped, *extra])


class Tariff(Protocol):
    def rate_at(self, when: datetime) -> RateInfo:
        """Price in effect at `when`, a naive local time."""


@dataclass(frozen=True)
class FlatTariff:
    rate: float

    def rate_at(self, when: datetime) -> RateInfo:
        return RateInfo(self.rate, Period.FLAT, None)


@dataclass(frozen=True)
class TouTariff:
    peak_rate: float
    offpeak_rate: float
    holidays: HolidayCalendar

    def rate_at(self, when: datetime) -> RateInfo:
        today = when.date()
        if self._is_peak_day(today) and PEAK_START <= when.time() < PEAK_END:
            return RateInfo(self.peak_rate, Period.PEAK, datetime.combine(today, PEAK_END))
        return RateInfo(self.offpeak_rate, Period.OFF_PEAK, self._next_peak_start(when))

    def _is_peak_day(self, d: date) -> bool:
        return d.weekday() < 5 and not self.holidays.is_holiday(d)

    def _next_peak_start(self, when: datetime) -> datetime | None:
        first = when.date() if when.time() < PEAK_START else when.date() + timedelta(days=1)
        for offset in range(LOOKAHEAD_DAYS):
            day = first + timedelta(days=offset)
            if self._is_peak_day(day):
                return datetime.combine(day, PEAK_START)
        return None


def tariff_from_settings(s: Settings, holidays: HolidayCalendar) -> Tariff:
    if s.meter_type is MeterType.TOU:
        calendar = HolidayCalendar(holidays.dates | set(s.extra_holidays))
        return TouTariff(s.tou_peak_rate, s.tou_offpeak_rate, calendar)
    return FlatTariff(standard_rate(s))
