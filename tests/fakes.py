import ctypes
from collections.abc import Mapping
from typing import Any

from power_meter.sensors.base import BatteryReading, Component, Reading
from power_meter.sensors.pdh import CounterValueItem


class FakeSource:
    """Returns the given readings in order, then keeps repeating the last one.

    An exception in `readings` or as `opens` is raised instead of returned.
    """

    def __init__(
        self,
        component: Component,
        readings: list[Reading | Exception | None],
        opens: bool | Exception = True,
    ) -> None:
        self.component = component
        self.readings = list(readings)
        self.opens = opens
        self.open_calls = 0
        self.read_calls = 0
        self.close_calls = 0

    def open(self) -> bool:
        self.open_calls += 1
        if isinstance(self.opens, Exception):
            raise self.opens
        return self.opens

    def read(self) -> Reading | None:
        self.read_calls += 1
        if len(self.readings) > 1:
            reading = self.readings.pop(0)
        else:
            reading = self.readings[0] if self.readings else None
        if isinstance(reading, Exception):
            raise reading
        return reading

    def close(self) -> None:
        self.close_calls += 1


class FakeBattery:
    def __init__(self, reading: BatteryReading | None, opens: bool = True) -> None:
        self.reading = reading
        self.opens = opens
        self.closed = False

    def open(self) -> bool:
        return self.opens

    def read(self) -> BatteryReading | None:
        return self.reading

    def close(self) -> None:
        self.closed = True


class FakeLoad:
    def __init__(self, cpu: float | None = None, gpus: Mapping[str, float] | None = None) -> None:
        self.cpu = cpu
        self.gpus = dict(gpus or {})

    def cpu_utilization(self) -> float | None:
        return self.cpu

    def gpu_utilizations(self) -> Mapping[str, float]:
        return self.gpus


PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_NO_COUNTER = 0xC0000BB9
PDH_CSTATUS_INVALID_DATA = 0xC0000BBA


class FakePdh:
    """Stands in for pdh.dll. `counters` maps English counter paths to instance -> value."""

    def __init__(self, counters: dict[str, dict[str, float]]) -> None:
        self.counters = counters
        self.calls: list[str] = []
        self.formats: list[int] = []
        self.collect_status = 0
        self.invalid: set[str] = set()
        self._paths: dict[int, str] = {}
        self._items: object = None

    def PdhOpenQueryW(self, source: None, user_data: int, query: Any) -> int:
        self.calls.append("PdhOpenQueryW")
        query.contents.value = 1
        return 0

    def PdhAddEnglishCounterW(self, query: Any, path: str, user_data: int, counter: Any) -> int:
        self.calls.append("PdhAddEnglishCounterW")
        if path not in self.counters:
            return PDH_CSTATUS_NO_COUNTER
        handle = 100 + len(self._paths)
        self._paths[handle] = path
        counter.contents.value = handle
        return 0

    def PdhCollectQueryData(self, query: Any) -> int:
        self.calls.append("PdhCollectQueryData")
        return self.collect_status

    def PdhGetFormattedCounterArrayW(
        self, counter: Any, fmt: int, size: Any, count: Any, buffer: Any
    ) -> int:
        self.calls.append("PdhGetFormattedCounterArrayW")
        self.formats.append(fmt)
        values = self.counters[self._paths[counter]]
        count.contents.value = len(values)
        if not values:
            return 0
        needed = ctypes.sizeof(CounterValueItem) * len(values)
        if size.contents.value < needed:
            size.contents.value = needed
            return PDH_MORE_DATA
        items = (CounterValueItem * len(values)).from_buffer(buffer)
        for item, (name, value) in zip(items, values.items(), strict=True):
            item.szName = name
            item.FmtValue.doubleValue = value
            item.FmtValue.CStatus = PDH_CSTATUS_INVALID_DATA if name in self.invalid else 0
        self._items = items
        return 0

    def PdhCloseQuery(self, query: Any) -> int:
        self.calls.append("PdhCloseQuery")
        return 0
