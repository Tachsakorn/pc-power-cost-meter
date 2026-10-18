import logging
import time
from collections.abc import Callable, Mapping
from typing import Final

from power_meter.sensors.base import Component, Reading
from power_meter.sensors.pdh import PdhQuery
from power_meter.sensors.warn_once import WarnOnce

ENERGY_METER_POWER: Final = r"\Energy Meter(*)\Power"

logger = logging.getLogger(__name__)


def sum_package_watts(values: Mapping[str, float]) -> float | None:
    """Total CPU package power from Energy Meter instances, or None if no package is listed."""
    milliwatts = [value for name, value in values.items() if name.lower().endswith("_pkg")]
    return sum(milliwatts) / 1000 if milliwatts else None


class EnergyMeterCpu:
    """CPU package power from the RAPL counters Windows publishes as the Energy Meter set."""

    component: Component = "cpu"

    def __init__(self, query_factory: Callable[[], PdhQuery] = PdhQuery) -> None:
        self._query_factory = query_factory
        self._query: PdhQuery | None = None
        self._counter = 0
        self._warn = WarnOnce(logger)

    def open(self) -> bool:
        try:
            query = self._query_factory()
        except OSError as exc:
            self._warn("Energy Meter unavailable: %s", exc)
            return False
        try:
            counter = query.add_english(ENERGY_METER_POWER)
            # PDH has no value for a rate counter until its second collect.
            query.collect()
            time.sleep(0.1)
            query.collect()
            watts = sum_package_watts(query.values(counter))
        except OSError as exc:
            query.close()
            self._warn("Energy Meter unavailable: %s", exc)
            return False
        if watts is None:
            query.close()
            self._warn("Energy Meter has no CPU package instance")
            return False
        self._query = query
        self._counter = counter
        return True

    def read(self) -> Reading | None:
        if self._query is None:
            return None
        try:
            self._query.collect()
            watts = sum_package_watts(self._query.values(self._counter))
        except OSError as exc:
            self._warn("Energy Meter read failed: %s", exc)
            return None
        if watts is None:
            self._warn("Energy Meter stopped reporting a CPU package instance")
            return None
        return Reading(watts, "Energy Meter (RAPL)", measured=True)

    def close(self) -> None:
        if self._query is not None:
            self._query.close()
            self._query = None
