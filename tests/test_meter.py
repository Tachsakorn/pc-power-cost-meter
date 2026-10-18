import pytest

from power_meter.meter import MAX_GAP_S, Meter, Totals


def test_first_sample_adds_nothing() -> None:
    meter = Meter()
    meter.add(500.0, 0.0, 4.5)
    assert meter.totals == Totals()
    assert meter.session == Totals()


def test_constant_power_one_hour() -> None:
    meter = Meter()
    for second in range(3601):
        meter.add(1000.0, float(second), 4.5)
    assert meter.totals.energy_kwh == pytest.approx(1.0, rel=1e-9)
    assert meter.totals.cost_thb == pytest.approx(4.5, rel=1e-9)
    assert meter.totals.elapsed_s == pytest.approx(3600.0)


def test_trapezoid() -> None:
    meter = Meter()
    meter.add(0.0, 0.0, 1.0)
    meter.add(100.0, 1.0, 1.0)
    assert meter.totals.energy_kwh == pytest.approx(50 / 3_600_000)


def test_gap_over_5s_skipped() -> None:
    meter = Meter()
    for t in (0.0, 1.0, 10.0, 11.0):
        meter.add(100.0, t, 4.5)
    assert meter.totals.elapsed_s == pytest.approx(2.0)


def test_gap_of_exactly_limit_is_counted() -> None:
    meter = Meter()
    meter.add(100.0, 0.0, 4.5)
    meter.add(100.0, MAX_GAP_S, 4.5)
    assert meter.totals.elapsed_s == pytest.approx(MAX_GAP_S)


def test_non_monotonic_time_skipped() -> None:
    meter = Meter()
    meter.add(100.0, 5.0, 4.5)
    meter.add(100.0, 4.0, 4.5)
    assert meter.totals == Totals()


def test_repeated_timestamp_skipped() -> None:
    meter = Meter()
    meter.add(100.0, 5.0, 4.5)
    meter.add(100.0, 5.0, 4.5)
    assert meter.totals == Totals()


def test_rate_applies_per_interval() -> None:
    meter = Meter()
    meter.add(1000.0, 0.0, 6.38)
    for second in range(1, 1801):
        meter.add(1000.0, float(second), 6.38)
    for second in range(1801, 3601):
        meter.add(1000.0, float(second), 3.00)
    assert meter.totals.cost_thb == pytest.approx(0.5 * 6.38 + 0.5 * 3.00)


def test_persisted_totals_carry_over_and_session_starts_zero() -> None:
    meter = Meter(Totals(cost_thb=10.0, energy_kwh=2.0, elapsed_s=7200.0))
    assert meter.totals == Totals(10.0, 2.0, 7200.0)
    assert meter.session == Totals()

    meter.add(1000.0, 0.0, 4.0)
    meter.add(1000.0, 3600.0 / 1000, 4.0)
    assert meter.session.elapsed_s == pytest.approx(3.6)
    assert meter.totals.elapsed_s == pytest.approx(7203.6)
    assert meter.totals.energy_kwh == pytest.approx(2.0 + meter.session.energy_kwh)
    assert meter.totals.cost_thb == pytest.approx(10.0 + meter.session.cost_thb)


def test_reset_zeroes_everything() -> None:
    meter = Meter(Totals(cost_thb=10.0, energy_kwh=2.0, elapsed_s=7200.0))
    meter.add(100.0, 0.0, 4.5)
    meter.add(100.0, 1.0, 4.5)
    meter.reset()
    assert meter.totals == Totals()
    assert meter.session == Totals()


def test_first_sample_after_reset_only_records() -> None:
    meter = Meter()
    meter.add(100.0, 0.0, 4.5)
    meter.add(100.0, 1.0, 4.5)
    meter.reset()
    meter.add(100.0, 2.0, 4.5)
    assert meter.totals == Totals()
    meter.add(100.0, 3.0, 4.5)
    assert meter.totals.elapsed_s == pytest.approx(1.0)
