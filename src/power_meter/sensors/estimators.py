from power_meter.sensors.base import Component, LoadProvider, Reading

_CPU_IDLE_SHARE = 0.30
_GPU_IDLE_SHARE = 0.10


class CpuEstimate:
    """CPU power from utilisation: an idle floor plus a share scaling linearly to TDP."""

    component: Component = "cpu"

    def __init__(self, load: LoadProvider, tdp_w: float) -> None:
        self._load = load
        self._tdp_w = tdp_w

    def set_tdp(self, tdp_w: float) -> None:
        self._tdp_w = tdp_w

    def open(self) -> bool:
        return True

    def read(self) -> Reading:
        util = self._load.cpu_utilization() or 0.0
        watts = self._tdp_w * (_CPU_IDLE_SHARE + (1 - _CPU_IDLE_SHARE) * util)
        return Reading(watts, "Estimate", measured=False)

    def close(self) -> None:
        pass


class GpuEstimate:
    """GPU power from 3D utilisation, summed over every adapter the load provider reports."""

    component: Component = "gpu"

    def __init__(self, load: LoadProvider, tdp_w: float) -> None:
        self._load = load
        self._tdp_w = tdp_w

    def set_tdp(self, tdp_w: float) -> None:
        self._tdp_w = tdp_w

    def open(self) -> bool:
        return True

    def read(self) -> Reading:
        watts = sum(
            self._tdp_w * (_GPU_IDLE_SHARE + (1 - _GPU_IDLE_SHARE) * util)
            for util in self._load.gpu_utilizations().values()
        )
        return Reading(watts, "Estimate", measured=False)

    def close(self) -> None:
        pass
