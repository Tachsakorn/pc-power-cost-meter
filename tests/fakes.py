from collections.abc import Mapping

from power_meter.sensors.base import BatteryReading, Component, Reading


class FakeSource:
    """Returns the given readings in order, then keeps repeating the last one."""

    def __init__(
        self, component: Component, readings: list[Reading | None], opens: bool = True
    ) -> None:
        self.component = component
        self.readings = list(readings)
        self.opens = opens
        self.open_calls = 0
        self.read_calls = 0
        self.close_calls = 0

    def open(self) -> bool:
        self.open_calls += 1
        return self.opens

    def read(self) -> Reading | None:
        self.read_calls += 1
        if len(self.readings) > 1:
            return self.readings.pop(0)
        return self.readings[0] if self.readings else None

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
