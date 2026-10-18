from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

Component = Literal["cpu", "gpu"]


@dataclass(frozen=True)
class Reading:
    watts: float
    source: str
    measured: bool


class PowerSource(Protocol):
    component: Component

    def open(self) -> bool:
        """Return False when the source isn't available on this PC."""
        ...

    def read(self) -> Reading | None:
        """Return None on a transient failure."""
        ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class BatteryReading:
    on_ac: bool
    rate_w: float | None  # positive while discharging


class BatterySource(Protocol):
    def open(self) -> bool: ...

    def read(self) -> BatteryReading | None: ...

    def close(self) -> None: ...


class LoadProvider(Protocol):
    def cpu_utilization(self) -> float | None:
        """Fraction 0..1, or None when unknown."""
        ...

    def gpu_utilizations(self) -> Mapping[str, float]:
        """3D engine utilisation 0..1 for every 3D-capable adapter PDH sees, keyed by LUID.

        The GPU estimate uses only the busiest adapter.
        """
        ...
