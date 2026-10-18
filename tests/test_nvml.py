import logging
import sys

import pynvml
import pytest

from power_meter.sensors.nvidia_nvml import NvmlGpu


class FakeNvml:
    """Stands in for the pynvml module. `milliwatts` has one entry per device; None marks a
    device that doesn't support power readings."""

    NVMLError = pynvml.NVMLError

    def __init__(self, milliwatts: list[int | None]) -> None:
        self.milliwatts = milliwatts
        self.init_error: int | None = None
        self.read_error: int | None = None
        self.inits = 0
        self.shutdowns = 0
        self.power_calls: list[int] = []

    def nvmlInit(self) -> None:
        if self.init_error is not None:
            raise pynvml.NVMLError(self.init_error)
        self.inits += 1

    def nvmlShutdown(self) -> None:
        self.shutdowns += 1

    def nvmlDeviceGetCount(self) -> int:
        return len(self.milliwatts)

    def nvmlDeviceGetHandleByIndex(self, index: int) -> int:
        return index

    def nvmlDeviceGetPowerUsage(self, handle: int) -> int:
        self.power_calls.append(handle)
        if self.read_error is not None:
            raise pynvml.NVMLError(self.read_error)
        value = self.milliwatts[handle]
        if value is None:
            raise pynvml.NVMLError(pynvml.NVML_ERROR_NOT_SUPPORTED)
        return value


def test_nvml_unavailable_without_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pynvml", None)
    assert not NvmlGpu().open()


def test_nvml_unavailable_when_init_fails(caplog: pytest.LogCaptureFixture) -> None:
    nvml = FakeNvml([20_000])
    nvml.init_error = pynvml.NVML_ERROR_DRIVER_NOT_LOADED
    with caplog.at_level(logging.WARNING):
        assert not NvmlGpu(nvml).open()
    assert "Driver Not Loaded" in caplog.text
    assert nvml.shutdowns == 0


def test_nvml_sums_devices() -> None:
    nvml = FakeNvml([12_500, 30_000])
    gpu = NvmlGpu(nvml)
    assert gpu.open()
    nvml.power_calls.clear()
    reading = gpu.read()
    assert reading is not None
    assert reading.watts == 42.5
    assert reading.source == "NVIDIA NVML"
    assert reading.measured
    assert gpu.component == "gpu"
    assert nvml.power_calls == [0, 1]


def test_nvml_not_supported_device_unavailable() -> None:
    nvml = FakeNvml([12_500, None])
    assert not NvmlGpu(nvml).open()
    assert nvml.shutdowns == 1


def test_nvml_without_devices_unavailable() -> None:
    nvml = FakeNvml([])
    assert not NvmlGpu(nvml).open()
    assert nvml.shutdowns == 1


def test_nvml_read_failure_returns_none_and_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    nvml = FakeNvml([12_500])
    gpu = NvmlGpu(nvml)
    assert gpu.open()
    nvml.read_error = pynvml.NVML_ERROR_GPU_IS_LOST
    with caplog.at_level(logging.WARNING):
        assert gpu.read() is None
        assert gpu.read() is None
    assert len(caplog.records) == 1
    assert "GPU is lost" in caplog.text


def test_nvml_close_shuts_down_once() -> None:
    nvml = FakeNvml([12_500])
    gpu = NvmlGpu(nvml)
    assert gpu.open()
    gpu.close()
    gpu.close()
    assert nvml.shutdowns == 1
    assert gpu.read() is None


def test_nvml_reopens_after_close() -> None:
    nvml = FakeNvml([12_500])
    gpu = NvmlGpu(nvml)
    assert gpu.open()
    gpu.close()
    assert gpu.open()
    assert gpu.read() is not None
    assert nvml.inits == 2
