from typing import Final

from power_meter.meter import Totals

MIN_SESSION_S: Final = 60.0
_DAYS_PER_MONTH: Final = 30


def monthly_cost(session: Totals, hours_per_day: int) -> float | None:
    """Extrapolates this run's average hourly cost to a 30-day month, or None if too short."""
    if session.elapsed_s < MIN_SESSION_S:
        return None
    return session.cost_thb / (session.elapsed_s / 3600) * hours_per_day * _DAYS_PER_MONTH
