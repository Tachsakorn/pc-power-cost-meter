from dataclasses import replace

from power_meter.meter import Totals
from power_meter.power_model import Part, PowerSnapshot
from power_meter.sampler import MeterState
from power_meter.settings import default_settings
from power_meter.tariff import Period, RateInfo
from power_meter.ui.main_view import build_view_model

SETTINGS = default_settings(is_laptop=False)

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
    rate=RateInfo(rate=4.5, period=Period.FLAT, next_change=None),
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

    vm = build_view_model(state, SETTINGS)

    assert vm.spent == "฿ 12.5000"
    assert vm.spent_caption == "in 10h 0m"
    assert vm.power == "—"
    assert vm.per_hour == ""
    assert vm.usage_fraction == 0.0
    assert vm.details == ()
    assert vm.monthly == "Measuring…"


def test_running_values() -> None:
    vm = build_view_model(RUNNING, SETTINGS)

    assert vm.spent == "฿ 1.2847"
    assert vm.spent_caption == "in 2h 15m"
    assert vm.power == "247 W"
    assert vm.per_hour == "about ฿1.11 per hour"
    assert vm.usage_label == "Gaming-level use"
    assert vm.monthly == "≈ ฿85.65 per month"
    assert vm.hours_per_day == 5
    assert vm.details == (
        ("CPU", "50 W", "measured"),
        ("GPU", "140 W", "measured"),
        ("Other", "25 W", "estimated"),
        ("PSU loss", "32 W", "87% efficiency"),
    )
    assert vm.price == "Price: Normal meter · ฿4.50 per unit (default)"
    assert vm.notice is None


def test_per_hour_uses_smoothed_watts() -> None:
    vm = build_view_model(replace(RUNNING, display_w=200.0), SETTINGS)

    assert vm.power == "200 W"
    assert vm.per_hour == "about ฿0.90 per hour"


def test_monthly_follows_hours_per_day() -> None:
    vm = build_view_model(RUNNING, replace(SETTINGS, hours_per_day=24))

    assert vm.monthly == "≈ ฿411 per month"
    assert vm.hours_per_day == 24


def test_monthly_measuring() -> None:
    short_session = Totals(cost_thb=0.002, energy_kwh=0.0004, elapsed_s=30.0)
    state = replace(RUNNING, session=short_session)

    assert build_view_model(state, SETTINGS).monthly == "Measuring…"


def test_restarting_notice() -> None:
    state = replace(RUNNING, status="restarting")

    vm = build_view_model(state, SETTINGS)

    assert vm.notice == "Measurement paused — restarting"
    assert vm.power == "247 W"


def test_restarting_takes_priority_over_estimates() -> None:
    estimated = replace(SNAPSHOT, all_estimated=True)
    state = replace(RUNNING, snapshot=estimated, status="restarting")

    assert build_view_model(state, SETTINGS).notice == "Measurement paused — restarting"


def test_all_estimated_notice() -> None:
    state = replace(RUNNING, snapshot=replace(SNAPSHOT, all_estimated=True))

    assert build_view_model(state, SETTINGS).notice == "Estimates only on this PC"


def test_no_estimates_notice_on_battery() -> None:
    battery = replace(
        SNAPSHOT,
        parts=(Part("Battery", 30.0, True, "measured"),),
        on_battery=True,
        all_estimated=True,
    )

    assert build_view_model(replace(RUNNING, snapshot=battery), SETTINGS).notice is None


def test_usage_fraction_clamped() -> None:
    def fraction(wall_w: float, peak_w: float) -> float:
        snapshot = replace(SNAPSHOT, wall_w=wall_w, peak_w=peak_w)
        return build_view_model(replace(RUNNING, snapshot=snapshot), SETTINGS).usage_fraction

    assert fraction(150.0, 300.0) == 0.5
    assert fraction(400.0, 300.0) == 1.0
    assert fraction(-5.0, 300.0) == 0.0
    assert fraction(100.0, 0.0) == 0.0
