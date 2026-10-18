import logging
import re
from collections.abc import Callable, Mapping
from typing import Final

from power_meter.sensors.adapters import discrete_gpu_luids
from power_meter.sensors.pdh import PdhQuery
from power_meter.sensors.warn_once import WarnOnce

CPU_UTILITY: Final = r"\Processor Information(_Total)\% Processor Utility"
GPU_3D: Final = r"\GPU Engine(*engtype_3D)\Utilization Percentage"

_LUID = re.compile(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+)", re.IGNORECASE)

logger = logging.getLogger(__name__)


def _fraction(percent: float) -> float:
    return min(max(percent / 100, 0.0), 1.0)


def gpu_util_by_luid(values: Mapping[str, float]) -> dict[str, float]:
    """3D utilisation per adapter (0..1) from GPU Engine instances, which are per process."""
    totals: dict[str, float] = {}
    for name, percent in values.items():
        match = _LUID.search(name)
        if match:
            luid = match.group(1).lower()
            totals[luid] = totals.get(luid, 0.0) + percent
    return {luid: _fraction(percent) for luid, percent in totals.items()}


class PdhLoad:
    """CPU and GPU utilisation from PDH counters, sampled once per refresh()."""

    def __init__(
        self,
        query_factory: Callable[[], PdhQuery] = PdhQuery,
        discrete_gpus: Callable[[], frozenset[str] | None] = discrete_gpu_luids,
    ) -> None:
        self._query_factory = query_factory
        self._discrete_gpus = discrete_gpus
        self._query: PdhQuery | None = None
        self._cpu_counter: int | None = None
        self._gpu_counter: int | None = None
        self._discrete: frozenset[str] | None = None
        self._cpu: float | None = None
        self._gpus: dict[str, float] = {}
        self._warn = WarnOnce(logger)

    def open(self) -> bool:
        try:
            query = self._query_factory()
        except OSError as exc:
            self._warn("Utilisation counters unavailable: %s", exc)
            return False
        cpu_counter = self._add(query, CPU_UTILITY)
        gpu_counter = self._add(query, GPU_3D)
        if cpu_counter is None and gpu_counter is None:
            query.close()
            return False
        try:
            query.collect()
        except OSError as exc:
            query.close()
            self._warn("Utilisation counters unavailable: %s", exc)
            return False

        self._query = query
        self._cpu_counter = cpu_counter
        self._gpu_counter = gpu_counter
        self._discrete = self._discrete_gpus()
        if self._discrete is None:
            logger.warning("GPU adapter types unknown; counting 3D load on every adapter")
        return True

    def refresh(self) -> None:
        if self._query is None:
            return
        try:
            self._query.collect()
            if self._cpu_counter is not None:
                cpu = self._query.values(self._cpu_counter)
                self._cpu = _fraction(next(iter(cpu.values()))) if cpu else None
            if self._gpu_counter is not None:
                gpus = gpu_util_by_luid(self._query.values(self._gpu_counter))
                if self._discrete is not None:
                    gpus = {luid: util for luid, util in gpus.items() if luid in self._discrete}
                self._gpus = gpus
        except Exception as exc:  # runs every sample; a bad counter must not stop the sampler
            self._warn("Utilisation refresh failed: %s", exc)

    def cpu_utilization(self) -> float | None:
        return self._cpu

    def gpu_utilizations(self) -> Mapping[str, float]:
        return self._gpus

    def close(self) -> None:
        if self._query is not None:
            self._query.close()
            self._query = None

    def _add(self, query: PdhQuery, path: str) -> int | None:
        try:
            return query.add_english(path)
        except OSError as exc:
            self._warn("Counter %s unavailable: %s", path, exc)
            return None
