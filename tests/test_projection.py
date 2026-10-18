import pytest

from power_meter.meter import Totals
from power_meter.projection import monthly_cost


def test_monthly_none_before_60s() -> None:
    assert monthly_cost(Totals(cost_thb=1.0, elapsed_s=59.9), 5) is None


def test_monthly_available_at_60s() -> None:
    assert monthly_cost(Totals(cost_thb=0.5, elapsed_s=60.0), 5) is not None


def test_monthly_value() -> None:
    session = Totals(cost_thb=1.11, energy_kwh=0.25, elapsed_s=3600.0)
    assert monthly_cost(session, 5) == pytest.approx(166.5)
