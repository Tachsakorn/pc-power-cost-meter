"""Power draw of NVIDIA GPUs through NVML, using NVIDIA's nvidia-ml-py bindings."""

import logging
from typing import Any

from power_meter.sensors.base import Component, Reading
from power_meter.sensors.warn_once import WarnOnce

logger = logging.getLogger(__name__)


class NvmlGpu:
    """Measured power summed over every NVIDIA GPU."""

    component: Component = "gpu"

    def __init__(self, nvml: object | None = None) -> None:
        self._nvml: Any = nvml
        self._handles: list[object] = []
        self._initialized = False
        self._warn = WarnOnce(logger)

    def open(self) -> bool:
        if self._nvml is None:
            try:
                import pynvml
            except ImportError as exc:
                self._warn("NVIDIA NVML unavailable: %s", exc)
                return False
            self._nvml = pynvml
        nvml = self._nvml
        try:
            nvml.nvmlInit()
        except nvml.NVMLError as exc:
            self._warn("NVIDIA NVML unavailable: %s", exc)
            return False
        self._initialized = True
        try:
            handles = [nvml.nvmlDeviceGetHandleByIndex(i) for i in range(nvml.nvmlDeviceGetCount())]
            # A GPU without a power sensor would make the total silently low.
            for handle in handles:
                nvml.nvmlDeviceGetPowerUsage(handle)
        except nvml.NVMLError as exc:
            self._warn("NVML can't read GPU power: %s", exc)
            self.close()
            return False
        if not handles:
            self._warn("NVML reports no NVIDIA GPU")
            self.close()
            return False
        self._handles = handles
        return True

    def read(self) -> Reading | None:
        if not self._handles:
            return None
        nvml = self._nvml
        try:
            milliwatts = sum(nvml.nvmlDeviceGetPowerUsage(handle) for handle in self._handles)
        except nvml.NVMLError as exc:
            self._warn("NVML power read failed: %s", exc)
            return None
        return Reading(milliwatts / 1000, "NVIDIA NVML", measured=True)

    def close(self) -> None:
        self._handles = []
        if not self._initialized:
            return
        self._initialized = False
        try:
            self._nvml.nvmlShutdown()
        except self._nvml.NVMLError as exc:
            self._warn("nvmlShutdown failed: %s", exc)
