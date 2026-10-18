import pytest
from fakes import FakeLoad

from power_meter.sensors.estimators import CpuEstimate, GpuEstimate


def test_cpu_estimate_formula() -> None:
    reading = CpuEstimate(FakeLoad(cpu=0.5), tdp_w=65).read()
    assert reading.watts == pytest.approx(42.25)
    assert reading.source == "Estimate"
    assert not reading.measured


def test_cpu_estimate_util_unknown() -> None:
    assert CpuEstimate(FakeLoad(cpu=None), tdp_w=65).read().watts == pytest.approx(19.5)


def test_gpu_estimate_sums_adapters() -> None:
    load = FakeLoad(gpus={"luid-a": 0.0, "luid-b": 1.0})
    assert GpuEstimate(load, tdp_w=100).read().watts == pytest.approx(10 + 100)


def test_gpu_estimate_without_adapters_is_zero() -> None:
    assert GpuEstimate(FakeLoad(), tdp_w=100).read().watts == 0


def test_estimators_always_open() -> None:
    assert CpuEstimate(FakeLoad(), 65).open()
    assert GpuEstimate(FakeLoad(), 150).open()


def test_set_tdp() -> None:
    cpu = CpuEstimate(FakeLoad(cpu=1.0), tdp_w=65)
    cpu.set_tdp(100)
    assert cpu.read().watts == pytest.approx(100)
    gpu = GpuEstimate(FakeLoad(gpus={"luid": 1.0}), tdp_w=150)
    gpu.set_tdp(200)
    assert gpu.read().watts == pytest.approx(200)
