import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, TypeGuard

from power_meter.sensors.base import BatterySource, Component, PowerSource, Reading
from power_meter.sensors.estimators import CpuEstimate, GpuEstimate
from power_meter.sensors.warn_once import WarnOnce
from power_meter.settings import Settings

FAILS_BEFORE_DEMOTE: Final = 3
RETRY_DEMOTED_S: Final = 60.0
MAX_VALID_W: Final = 1000.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Part:
    label: str
    watts: float
    measured: bool
    detail: str


@dataclass(frozen=True)
class PowerSnapshot:
    wall_w: float
    parts: tuple[Part, ...]
    on_battery: bool
    usage_label: str
    all_estimated: bool
    peak_w: float


def usage_label(wall_w: float, peak_w: float) -> str:
    ratio = wall_w / peak_w if peak_w > 0 else 0.0
    if ratio < 0.20:
        return "Idle"
    if ratio < 0.45:
        return "Light use"
    if ratio < 0.75:
        return "Heavy use"
    return "Gaming-level use"


def _is_valid(reading: Reading | None) -> TypeGuard[Reading]:
    return reading is not None and 0 <= reading.watts <= MAX_VALID_W


def _source_name(source: PowerSource) -> str:
    return type(source).__name__


class _SourceChain:
    """Sources for one component, best first, with an estimator last.

    A source that raises is treated like one that is unavailable or returned no reading, so a
    driver fault demotes it instead of stopping the sampler.
    """

    def __init__(self, component: Component, sources: Sequence[PowerSource]) -> None:
        self._component = component
        self.sources = sources
        self._warn = WarnOnce(logger)
        self._opened: set[int] = set()
        self._unavailable: set[int] = set()
        self._demoted: set[int] = set()
        self._fails = 0
        self._last_failure = ""
        self._last_good: Reading | None = None
        self._next_retry = 0.0
        self._active = self._first_available(0)

    def sample(self, now: float) -> Reading | None:
        reading = self._retry_demoted(now) or self._read_active(now)
        if reading is not None:
            self._last_good = reading
            return reading
        return self._last_good or self._read_fallback()

    def close(self) -> None:
        for index in self._opened:
            self._close(index)
        self._opened.clear()

    def _read_active(self, now: float) -> Reading | None:
        reading = self._read(self._active)
        if _is_valid(reading):
            self._fails = 0
            return reading
        self._fails += 1
        self._last_failure = (
            "no reading" if reading is None else f"out-of-range value {reading.watts:.0f} W"
        )
        if self._fails < FAILS_BEFORE_DEMOTE or self._active == len(self.sources) - 1:
            return None
        self._demote(now)
        return self._read_active(now)

    def _read_fallback(self) -> Reading | None:
        for index in range(self._active + 1, len(self.sources)):
            if self._ensure_open(index) and _is_valid(reading := self._read(index)):
                return reading
        return None

    def _demote(self, now: float) -> None:
        old = self._active
        self._close(old)
        self._opened.discard(old)
        self._next_retry = now + RETRY_DEMOTED_S
        self._demoted.add(old)
        self._active = self._first_available(old + 1)
        self._demoted.discard(self._active)
        self._fails = 0
        logger.warning(
            "%s source %s demoted after %d failed reads (last: %s); using %s",
            self._component.upper(),
            _source_name(self.sources[old]),
            FAILS_BEFORE_DEMOTE,
            self._last_failure,
            _source_name(self.sources[self._active]),
        )

    def _retry_demoted(self, now: float) -> Reading | None:
        candidates = [index for index in self._demoted if index < self._active]
        if not candidates or now < self._next_retry:
            return None
        self._next_retry = now + RETRY_DEMOTED_S
        index = min(candidates)
        if not self._open(index):
            return None
        reading = self._read(index)
        if not _is_valid(reading):
            self._close(index)
            return None
        logger.warning(
            "%s source %s promoted back after a successful retry (%.1f W)",
            self._component.upper(),
            _source_name(self.sources[index]),
            reading.watts,
        )
        self._opened.add(index)
        self._demoted.discard(index)
        self._active = index
        self._fails = 0
        return reading

    def _first_available(self, start: int) -> int:
        for index in range(start, len(self.sources)):
            if self._ensure_open(index):
                return index
        return len(self.sources) - 1

    def _ensure_open(self, index: int) -> bool:
        if index in self._opened:
            return True
        if index in self._unavailable:
            return False
        if self._open(index):
            self._opened.add(index)
            return True
        self._unavailable.add(index)
        return False

    def _open(self, index: int) -> bool:
        try:
            return self.sources[index].open()
        except Exception as exc:
            self._report(index, "open", exc)
            return False

    def _read(self, index: int) -> Reading | None:
        try:
            return self.sources[index].read()
        except Exception as exc:
            self._report(index, "read", exc)
            return None

    def _close(self, index: int) -> None:
        try:
            self.sources[index].close()
        except Exception as exc:
            self._report(index, "close", exc)

    def _report(self, index: int, action: str, exc: Exception) -> None:
        self._warn(
            "%s source %s failed to %s: %s",
            self._component.upper(),
            _source_name(self.sources[index]),
            action,
            exc,
        )


class PowerModel:
    """Combines per-component readings into wall power.

    Each chain is ordered best first and must end with an estimator, which is always available.
    """

    def __init__(
        self,
        chains: Mapping[Component, Sequence[PowerSource]],
        battery: BatterySource | None,
        settings: Settings,
        refresh: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._chains = {
            component: _SourceChain(component, sources) for component, sources in chains.items()
        }
        self._battery = battery if battery is not None and battery.open() else None
        self._settings = settings
        self._refresh = refresh
        self._clock = clock
        self._max_wall_w = 0.0

    def sample(self) -> PowerSnapshot:
        if self._refresh is not None:
            self._refresh()
        now = self._clock()
        cpu = self._read("cpu", now)
        gpu = self._read("gpu", now)
        # The GPU estimator reads 0 W only when no discrete adapter is present. An integrated
        # GPU draws from the CPU package, so it is shown as part of the CPU and the usage level
        # is judged against the CPU's power budget alone.
        has_discrete_gpu = gpu.measured or gpu.watts > 0
        s = self._settings

        battery_w = self._battery_discharge_w()
        if battery_w is not None:
            wall_w = battery_w / s.charge_efficiency + s.extra_w
            parts = [
                Part("Battery", battery_w, True, "measured"),
                Part(
                    "Charging loss",
                    battery_w * (1 / s.charge_efficiency - 1),
                    False,
                    _efficiency(s.charge_efficiency),
                ),
            ]
        else:
            dc_w = cpu.watts + gpu.watts + s.other_w
            wall_w = dc_w / s.psu_efficiency + s.extra_w
            parts = [
                _component_part("CPU", cpu),
                _component_part("GPU", gpu)
                if has_discrete_gpu
                else Part("GPU", 0.0, False, "included in CPU"),
                Part("Other", s.other_w, False, "estimated"),
                Part(
                    "PSU loss",
                    dc_w * (1 / s.psu_efficiency - 1),
                    False,
                    _efficiency(s.psu_efficiency),
                ),
            ]
        if s.extra_w > 0:
            parts.append(Part("Extras", s.extra_w, False, "added in settings"))

        self._max_wall_w = max(self._max_wall_w, wall_w)
        gpu_tdp_w = s.gpu_tdp_w if has_discrete_gpu else 0.0
        budget_w = (s.cpu_tdp_w + gpu_tdp_w + s.other_w) / s.psu_efficiency + s.extra_w
        peak_w = max(self._max_wall_w, budget_w)
        return PowerSnapshot(
            wall_w=wall_w,
            parts=tuple(parts),
            on_battery=battery_w is not None,
            usage_label=usage_label(wall_w, peak_w),
            all_estimated=not (cpu.measured or gpu.measured),
            peak_w=peak_w,
        )

    def update_settings(self, settings: Settings) -> None:
        self._settings = settings
        for chain in self._chains.values():
            for source in chain.sources:
                if isinstance(source, CpuEstimate):
                    source.set_tdp(settings.cpu_tdp_w)
                elif isinstance(source, GpuEstimate):
                    source.set_tdp(settings.gpu_tdp_w)

    def close(self) -> None:
        for chain in self._chains.values():
            chain.close()
        if self._battery is not None:
            self._battery.close()

    def _read(self, component: Component, now: float) -> Reading:
        chain = self._chains.get(component)
        reading = chain.sample(now) if chain is not None else None
        return reading or Reading(0.0, "None", measured=False)

    def _battery_discharge_w(self) -> float | None:
        if self._battery is None:
            return None
        reading = self._battery.read()
        if reading is None or reading.on_ac or reading.rate_w is None:
            return None
        return reading.rate_w if 0 < reading.rate_w <= MAX_VALID_W else None


def _component_part(label: str, reading: Reading) -> Part:
    detail = "measured" if reading.measured else "estimated"
    return Part(label, reading.watts, reading.measured, detail)


def _efficiency(value: float) -> str:
    return f"{round(value * 100)}% efficiency"
