from dataclasses import replace
from datetime import date, datetime

from power_meter.settings import MeterType, StandardBand, default_settings, standard_rate
from power_meter.tariff import (
    FlatTariff,
    HolidayCalendar,
    Period,
    RateInfo,
    TouTariff,
    tariff_from_settings,
)

NO_HOLIDAYS = HolidayCalendar([])


def tou(holidays: HolidayCalendar = NO_HOLIDAYS) -> TouTariff:
    return TouTariff(peak_rate=6.38, offpeak_rate=3.00, holidays=holidays)


def test_flat_rate_constant() -> None:
    assert FlatTariff(4.5).rate_at(datetime(2026, 10, 19, 12, 0)) == RateInfo(
        4.5, Period.FLAT, None
    )
    assert FlatTariff(4.5).rate_at(datetime(2026, 10, 17, 3, 0)) == RateInfo(4.5, Period.FLAT, None)


def test_tou_weekday_boundaries() -> None:
    t = tou()
    assert t.rate_at(datetime(2026, 10, 19, 8, 59, 59)).period is Period.OFF_PEAK
    assert t.rate_at(datetime(2026, 10, 19, 9, 0, 0)).period is Period.PEAK
    assert t.rate_at(datetime(2026, 10, 19, 21, 59, 59)).period is Period.PEAK
    assert t.rate_at(datetime(2026, 10, 19, 22, 0, 0)).period is Period.OFF_PEAK


def test_tou_rates_follow_period() -> None:
    t = tou()
    assert t.rate_at(datetime(2026, 10, 19, 12, 0)).rate == 6.38
    assert t.rate_at(datetime(2026, 10, 19, 23, 0)).rate == 3.00


def test_tou_weekend_is_offpeak() -> None:
    info = tou().rate_at(datetime(2026, 10, 17, 12, 0))
    assert info.period is Period.OFF_PEAK
    assert info.next_change == datetime(2026, 10, 19, 9, 0)


def test_tou_holiday_is_offpeak() -> None:
    t = tou(HolidayCalendar([date(2026, 10, 21)]))
    info = t.rate_at(datetime(2026, 10, 21, 12, 0))
    assert info.period is Period.OFF_PEAK
    assert info.next_change == datetime(2026, 10, 22, 9, 0)


def test_next_change_before_peak_same_day() -> None:
    assert tou().rate_at(datetime(2026, 10, 19, 6, 30)).next_change == datetime(2026, 10, 19, 9, 0)


def test_next_change_from_peak() -> None:
    assert tou().rate_at(datetime(2026, 10, 19, 10, 0)).next_change == datetime(2026, 10, 19, 22, 0)


def test_friday_night_next_change() -> None:
    assert tou().rate_at(datetime(2026, 10, 23, 23, 0)).next_change == datetime(2026, 10, 26, 9, 0)


def test_next_change_skips_holiday_after_weekend() -> None:
    t = tou(HolidayCalendar([date(2026, 10, 26)]))
    assert t.rate_at(datetime(2026, 10, 24, 12, 0)).next_change == datetime(2026, 10, 27, 9, 0)


def test_bundled_calendar_loads() -> None:
    calendar = HolidayCalendar.bundled()
    assert calendar.is_holiday(date(2026, 12, 10))
    assert calendar.is_holiday(date(2027, 5, 20))
    assert not calendar.is_holiday(date(2026, 10, 19))


def test_bundled_calendar_leaves_out_substitution_days() -> None:
    calendar = HolidayCalendar.bundled()
    assert not calendar.is_holiday(date(2026, 12, 7))
    assert not calendar.is_holiday(date(2027, 10, 25))


def test_bundled_calendar_accepts_extra_dates() -> None:
    calendar = HolidayCalendar.bundled(extra=[date(2026, 10, 16)])
    assert calendar.is_holiday(date(2026, 10, 16))
    assert calendar.is_holiday(date(2026, 12, 10))


def test_tariff_from_settings() -> None:
    standard = replace(default_settings(is_laptop=False), standard_band=StandardBand.OVER_400)
    flat = tariff_from_settings(standard, NO_HOLIDAYS)
    assert isinstance(flat, FlatTariff)
    assert flat.rate_at(datetime(2026, 10, 19, 12, 0)).rate == standard_rate(standard)

    tou_settings = replace(
        standard, meter_type=MeterType.TOU, tou_peak_rate=7.0, tou_offpeak_rate=2.5
    )
    t = tariff_from_settings(tou_settings, NO_HOLIDAYS)
    assert isinstance(t, TouTariff)
    assert t.rate_at(datetime(2026, 10, 19, 12, 0)) == RateInfo(
        7.0, Period.PEAK, datetime(2026, 10, 19, 22, 0)
    )
    assert t.rate_at(datetime(2026, 10, 19, 23, 0)).rate == 2.5


def test_tariff_from_settings_counts_user_holidays() -> None:
    s = replace(
        default_settings(is_laptop=False),
        meter_type=MeterType.TOU,
        extra_holidays=(date(2026, 10, 20),),
    )
    t = tariff_from_settings(s, HolidayCalendar([date(2026, 10, 21)]))
    assert t.rate_at(datetime(2026, 10, 20, 12, 0)).period is Period.OFF_PEAK
    assert t.rate_at(datetime(2026, 10, 21, 12, 0)).period is Period.OFF_PEAK
    assert t.rate_at(datetime(2026, 10, 20, 12, 0)).next_change == datetime(2026, 10, 22, 9, 0)
