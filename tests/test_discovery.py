import pytest
from fakes import FakeBattery, FakeLoad

from power_meter.sensors import discovery
from power_meter.sensors.estimators import CpuEstimate, GpuEstimate
from power_meter.settings import default_settings


class OpenableLoad(FakeLoad):
    def __init__(self) -> None:
        super().__init__(cpu=1.0, gpus={"0x00000000_0x0000c3f1": 1.0})
        self.opened = False

    def open(self) -> bool:
        self.opened = True
        return True


@pytest.fixture
def load(monkeypatch: pytest.MonkeyPatch) -> OpenableLoad:
    load = OpenableLoad()
    monkeypatch.setattr(discovery, "PdhLoad", lambda: load)
    monkeypatch.setattr(discovery, "EnergyMeterCpu", lambda: "rapl")
    monkeypatch.setattr(discovery, "AdlGpu", lambda: "adl")
    monkeypatch.setattr(discovery, "NvmlGpu", lambda: "nvml")
    monkeypatch.setattr(discovery, "WindowsBattery", lambda: FakeBattery(None))
    monkeypatch.setattr(discovery, "battery_present", lambda: False)
    return load


def test_discovery_chain_order(load: OpenableLoad) -> None:
    sources = discovery.build_sources(default_settings(is_laptop=False))
    cpu = sources.chains["cpu"]
    gpu = sources.chains["gpu"]
    assert cpu[:-1] == ["rapl"]
    assert isinstance(cpu[-1], CpuEstimate)
    assert gpu[:-1] == ["adl", "nvml"]
    assert isinstance(gpu[-1], GpuEstimate)


def test_estimators_use_the_shared_load_and_settings_tdp(load: OpenableLoad) -> None:
    settings = default_settings(is_laptop=False)
    sources = discovery.build_sources(settings)
    assert sources.load is load
    assert load.opened
    assert sources.chains["cpu"][-1].read().watts == settings.cpu_tdp_w
    assert sources.chains["gpu"][-1].read().watts == settings.gpu_tdp_w


def test_desktop_has_no_battery(load: OpenableLoad) -> None:
    sources = discovery.build_sources(default_settings(is_laptop=False))
    assert sources.battery is None
    assert not sources.is_laptop


def test_laptop_gets_a_battery_source(load: OpenableLoad, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "battery_present", lambda: True)
    sources = discovery.build_sources(default_settings(is_laptop=True))
    assert isinstance(sources.battery, FakeBattery)
    assert sources.is_laptop
