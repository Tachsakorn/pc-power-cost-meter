from dataclasses import dataclass

from power_meter.sensors.amd_adl import AdlGpu
from power_meter.sensors.base import BatterySource, Component, PowerSource
from power_meter.sensors.battery import WindowsBattery
from power_meter.sensors.energy_meter import EnergyMeterCpu
from power_meter.sensors.estimators import CpuEstimate, GpuEstimate
from power_meter.sensors.load import PdhLoad
from power_meter.sensors.nvidia_nvml import NvmlGpu
from power_meter.settings import Settings


@dataclass
class Sources:
    chains: dict[Component, list[PowerSource]]
    battery: BatterySource | None
    load: PdhLoad


def build_sources(settings: Settings, is_laptop: bool) -> Sources:
    """Every power source for this PC, best first, with the estimators as the fallback.

    The load provider is already open; refresh it once per sample and close it on exit.
    """
    load = PdhLoad()
    # The estimators still work without counters, reporting their idle floor.
    load.open()
    battery = WindowsBattery() if is_laptop else None
    chains: dict[Component, list[PowerSource]] = {
        "cpu": [EnergyMeterCpu(), CpuEstimate(load, settings.cpu_tdp_w)],
        "gpu": [AdlGpu(), NvmlGpu(), GpuEstimate(load, settings.gpu_tdp_w)],
    }
    return Sources(chains, battery, load)
