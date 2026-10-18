from dataclasses import replace
from datetime import datetime

import pytest

from power_meter.meter import Totals
from power_meter.power_model import Part, PowerSnapshot
from power_meter.sampler import MeterState
from power_meter.settings import MeterType, default_settings
from power_meter.tariff import Period, RateInfo
from power_meter.ui.main_view import DetailRow, build_view_model

SETTINGS = default_settings(is_laptop=False)
FLAT = RateInfo(rate=4.5, period=Period.FLAT, next_change=None)

SNAPSHOT = PowerSnapshot(
    wall_w=247.0,
    parts=(
        Part("CPU", 50.0, True, "measured"),
        Part("GPU", 140.0, True, "measured"),
        Part("Other", 25.0, False, "estimated"),
        Part("PSU loss", 32.1, False, "87% efficiency"),
    ),
    on_battery=False,
    usage_label="Gaming-level use",
    all_estimated=False,
    peak_w=300.0,
)

RUNNING = MeterState(
    snapshot=SNAPSHOT,
    totals=Totals(cost_thb=1.2847, energy_kwh=0.2855, elapsed_s=8100.0),
    session=Totals(cost_thb=1.2847, energy_kwh=0.2855, elapsed_s=8100.0),
    status="running",
    display_w=247.0,
)


def test_warming_up_shows_dash() -> None:
    persisted = Totals(cost_thb=12.5, energy_kwh=2.7, elapsed_s=36000.0)
    state = replace(
        RUNNING,
        snapshot=None,
        totals=persisted,
        session=Totals(),
        status="warming_up",
        display_w=None,
    )

    vm = build_view_model(state, SETTINGS, FLAT)

    assert vm.spent == "12.5000"
    assert vm.spent_caption == "in 10 h 0 min"
    assert vm.watts == "—"
    assert vm.load == ""
    assert vm.peak == ""
    assert vm.details == ()
    assert vm.monthly == "Measuring…"


def test_no_reading_shows_dashes() -> None:
    state = replace(RUNNING, status="warming_up", display_w=None)

    vm = build_view_model(state, SETTINGS, FLAT)

    assert vm.watts == "—"
    assert vm.per_hour == "—"
    assert vm.gauge_fraction == 0.0


def test_running_values() -> None:
    vm = build_view_model(RUNNING, SETTINGS, FLAT)

    assert vm.status == "Measuring"
    assert vm.watts == "247"
    assert vm.load == "Gaming-level use"
    assert vm.peak == "300 W"
    assert vm.spent == "1.2847"
    assert vm.spent_caption == "in 2 h 15 min"
    assert vm.per_hour == "฿1.11"
    assert vm.monthly == "≈ ฿85.65"
    assert vm.hours_per_day == 5
    assert [(row.label, row.watts, row.detail, row.measured) for row in vm.details] == [
        ("CPU", "50 W", "measured", True),
        ("GPU", "140 W", "measured", True),
        ("Other", "25 W", "estimated", False),
        ("PSU loss", "32 W", "87% efficiency", False),
    ]
    assert vm.price == "Normal meter · ฿4.50 per unit (default)"
    assert vm.notice is None


def test_spent_groups_thousands() -> None:
    state = replace(RUNNING, totals=Totals(cost_thb=1234.56789, energy_kwh=274.0, elapsed_s=9e5))

    assert build_view_model(state, SETTINGS, FLAT).spent == "1,234.5679"


def test_per_hour_uses_smoothed_watts() -> None:
    vm = build_view_model(replace(RUNNING, display_w=200.0), SETTINGS, FLAT)

    assert vm.watts == "200"
    assert vm.per_hour == "฿0.90"


def test_price_and_per_hour_use_the_given_rate() -> None:
    tou = replace(SETTINGS, meter_type=MeterType.TOU)
    peak = RateInfo(rate=6.38, period=Period.PEAK, next_change=datetime(2026, 10, 14, 22, 0))

    vm = build_view_model(RUNNING, tou, peak)

    assert vm.price.startswith("TOU · Peak ฿6.38 now")
    assert vm.per_hour == "฿1.58"


def test_monthly_follows_hours_per_day() -> None:
    vm = build_view_model(RUNNING, replace(SETTINGS, hours_per_day=24), FLAT)

    assert vm.monthly == "≈ ฿411"
    assert vm.hours_per_day == 24


def test_monthly_measuring() -> None:
    short_session = Totals(cost_thb=0.002, energy_kwh=0.0004, elapsed_s=30.0)
    state = replace(RUNNING, session=short_session)

    assert build_view_model(state, SETTINGS, FLAT).monthly == "Measuring…"


def test_detail_share_is_relative_to_the_largest_part() -> None:
    snapshot = replace(
        SNAPSHOT,
        parts=(Part("CPU", 54.0, True, "measured"), Part("GPU", 107.0, True, "measured")),
    )

    vm = build_view_model(replace(RUNNING, snapshot=snapshot), SETTINGS, FLAT)

    assert [row.share for row in vm.details] == [pytest.approx(0.505, abs=1e-3), 1.0]


def test_detail_share_is_zero_when_every_part_is_zero() -> None:
    snapshot = replace(SNAPSHOT, parts=(Part("CPU", 0.0, True, "measured"),))

    vm = build_view_model(replace(RUNNING, snapshot=snapshot), SETTINGS, FLAT)

    assert vm.details == (DetailRow("CPU", "0 W", "measured", True, 0.0),)


def test_gauge_follows_the_averaged_reading() -> None:
    state = replace(RUNNING, snapshot=replace(SNAPSHOT, wall_w=150.0), display_w=90.0)

    assert build_view_model(state, SETTINGS, FLAT).gauge_fraction == pytest.approx(0.3)


def test_gauge_fraction_is_clamped() -> None:
    def fraction(display_w: float, peak_w: float) -> float:
        state = replace(RUNNING, snapshot=replace(SNAPSHOT, peak_w=peak_w), display_w=display_w)
        return build_view_model(state, SETTINGS, FLAT).gauge_fraction

    assert fraction(150.0, 300.0) == 0.5
    assert fraction(400.0, 300.0) == 1.0
    assert fraction(-5.0, 300.0) == 0.0
    assert fraction(100.0, 0.0) == 0.0


def test_no_peak_label_without_a_peak() -> None:
    state = replace(RUNNING, snapshot=replace(SNAPSHOT, peak_w=0.0))

    assert build_view_model(state, SETTINGS, FLAT).peak == ""


def test_restarting_notice() -> None:
    state = replace(RUNNING, status="restarting")

    vm = build_view_model(state, SETTINGS, FLAT)

    assert vm.notice == "Measurement paused — restarting"
    assert vm.status == "Measurement paused — restarting"
    assert vm.watts == "247"


def test_restarting_takes_priority_over_estimates() -> None:
    estimated = replace(SNAPSHOT, all_estimated=True)
    state = replace(RUNNING, snapshot=estimated, status="restarting")

    assert build_view_model(state, SETTINGS, FLAT).notice == "Measurement paused — restarting"


def test_all_estimated_notice() -> None:
    state = replace(RUNNING, snapshot=replace(SNAPSHOT, all_estimated=True))

    assert build_view_model(state, SETTINGS, FLAT).notice == "Estimates only on this PC"


def test_cannot_save_notice() -> None:
    vm = build_view_model(RUNNING, SETTINGS, FLAT, can_save=False)

    assert vm.notice == "Settings can't be saved on this PC"
    assert vm.status == "Settings can't be saved on this PC"


def test_cannot_save_takes_priority_over_estimates() -> None:
    state = replace(RUNNING, snapshot=replace(SNAPSHOT, all_estimated=True))

    assert build_view_model(state, SETTINGS, FLAT, can_save=False).notice == (
        "Settings can't be saved on this PC"
    )


def test_restarting_takes_priority_over_cannot_save() -> None:
    state = replace(RUNNING, status="restarting")

    assert build_view_model(state, SETTINGS, FLAT, can_save=False).notice == (
        "Measurement paused — restarting"
    )


def test_no_estimates_notice_on_battery() -> None:
    battery = replace(
        SNAPSHOT,
        parts=(Part("Battery", 30.0, True, "measured"),),
        on_battery=True,
        all_estimated=True,
    )

    assert build_view_model(replace(RUNNING, snapshot=battery), SETTINGS, FLAT).notice is None
