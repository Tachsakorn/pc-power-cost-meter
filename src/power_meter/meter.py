from dataclasses import dataclass
from typing import Final

MAX_GAP_S: Final = 5.0
_WATT_SECONDS_PER_KWH: Final = 3_600_000


@dataclass(frozen=True)
class Totals:
    cost_thb: float = 0.0
    energy_kwh: float = 0.0
    elapsed_s: float = 0.0


class Meter:
    """Integrates wall power samples into energy and cost.

    Intervals longer than MAX_GAP_S (sleep, a stalled sampler) are not billed.
    """

    def __init__(self, persisted: Totals | None = None) -> None:
        self._persisted = persisted or Totals()
        self._session = Totals()
        self._last: tuple[float, float] | None = None

    def add(self, wall_w: float, mono_s: float, rate: float) -> None:
        last, self._last = self._last, (wall_w, mono_s)
        if last is None:
            return
        prev_w, prev_s = last
        dt = mono_s - prev_s
        if dt <= 0 or dt > MAX_GAP_S:
            return
        kwh = (prev_w + wall_w) / 2 * dt / _WATT_SECONDS_PER_KWH
        self._session = Totals(
            self._session.cost_thb + kwh * rate,
            self._session.energy_kwh + kwh,
            self._session.elapsed_s + dt,
        )

    def reset(self) -> None:
        self._persisted = Totals()
        self._session = Totals()
        self._last = None

    @property
    def totals(self) -> Totals:
        return Totals(
            self._persisted.cost_thb + self._session.cost_thb,
            self._persisted.energy_kwh + self._session.energy_kwh,
            self._persisted.elapsed_s + self._session.elapsed_s,
        )

    @property
    def session(self) -> Totals:
        return self._session
