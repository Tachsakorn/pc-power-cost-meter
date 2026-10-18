from dataclasses import replace
from datetime import datetime

from power_meter.settings import MeterType, StandardBand, default_settings
from power_meter.tariff import Period, RateInfo
from power_meter.ui.format import baht, baht_short, duration, price_line, watts

MONDAY_NOON = datetime(2026, 10, 19, 12, 0)
SATURDAY_NOON = datetime(2026, 10, 17, 12, 0)


def test_baht_four_decimals() -> None:
    assert baht(1.2847) == "฿ 1.2847"
    assert baht(0) == "฿ 0.0000"


def test_baht_groups_thousands_and_honours_decimals() -> None:
    assert baht(1234.5678) == "฿ 1,234.5678"
    assert baht(12.3456, decimals=2) == "฿ 12.35"


def test_baht_short_drops_decimals_from_one_hundred() -> None:
    assert baht_short(1.11) == "฿1.11"
    assert baht_short(99.994) == "฿99.99"
    assert baht_short(99.996) == "฿100"
    assert baht_short(165.6) == "฿166"
    assert baht_short(1234.2) == "฿1,234"


def test_duration_formats() -> None:
    assert duration(8100) == "2h 15m"
    assert duration(2700) == "45m"
    assert duration(59.9) == "under 1m"
    assert duration(0) == "under 1m"
    assert duration(3600) == "1h 0m"
    assert duration(26 * 3600 + 5 * 60) == "26h 5m"


def test_watts_rounds_to_whole_number() -> None:
    assert watts(247.4) == "247 W"
    assert watts(0) == "0 W"


def test_price_line_standard_default() -> None:
    settings = default_settings(is_laptop=False)
    info = RateInfo(4.50, Period.FLAT, None)
    assert price_line(settings, info) == "Normal meter · ฿4.50 per unit (default)"


def test_price_line_standard_known_band() -> None:
    settings = replace(default_settings(False), standard_band=StandardBand.BAND_201_400)
    info = RateInfo(4.62, Period.FLAT, None)
    assert price_line(settings, info) == "Normal meter · ฿4.62 per unit"


def test_price_line_tou_peak() -> None:
    settings = replace(default_settings(False), meter_type=MeterType.TOU)
    info = RateInfo(6.38, Period.PEAK, datetime(2026, 10, 19, 22, 0))
    assert price_line(settings, info, now=MONDAY_NOON) == (
        "TOU · Peak ฿6.38 now · Off-peak from 22:00"
    )


def test_price_line_tou_offpeak_later_today() -> None:
    settings = replace(default_settings(False), meter_type=MeterType.TOU)
    info = RateInfo(3.00, Period.OFF_PEAK, datetime(2026, 10, 19, 9, 0))
    early = datetime(2026, 10, 19, 6, 30)
    assert price_line(settings, info, now=early) == "TOU · Off-peak ฿3.00 now · Peak from 09:00"


def test_price_line_tou_offpeak_weekend() -> None:
    settings = replace(default_settings(False), meter_type=MeterType.TOU)
    info = RateInfo(3.00, Period.OFF_PEAK, datetime(2026, 10, 19, 9, 0))
    assert price_line(settings, info, now=SATURDAY_NOON) == (
        "TOU · Off-peak ฿3.00 now · Peak from Mon 09:00"
    )


def test_price_line_tou_without_known_change() -> None:
    settings = replace(default_settings(False), meter_type=MeterType.TOU)
    info = RateInfo(3.00, Period.OFF_PEAK, None)
    assert price_line(settings, info, now=SATURDAY_NOON) == "TOU · Off-peak ฿3.00 now"
