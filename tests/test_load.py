import logging

import pytest
from fakes import FakePdh

from power_meter.sensors.load import CPU_UTILITY, GPU_3D, PdhLoad
from power_meter.sensors.pdh import PdhQuery

DISCRETE = "0x00000000_0x00015061"
INTEGRATED = "0x00000000_0x0000d1a6"


def engine(pid: int, luid: str) -> str:
    return f"pid_{pid}_luid_{luid}_phys_0_eng_0_engtype_3D"


def make_dll(cpu: float = 42.0, discrete: float = 55.0, integrated: float = 20.0) -> FakePdh:
    return FakePdh(
        {
            CPU_UTILITY: {"_Total": cpu},
            GPU_3D: {engine(10, DISCRETE): discrete, engine(20, INTEGRATED): integrated},
        }
    )


def open_load(dll: FakePdh, discrete: frozenset[str] | None = frozenset({DISCRETE})) -> PdhLoad:
    load = PdhLoad(lambda: PdhQuery(dll), discrete_gpus=lambda: discrete)
    assert load.open()
    return load


def test_cpu_utilization_is_a_fraction() -> None:
    load = open_load(make_dll(cpu=42.0))
    load.refresh()
    assert load.cpu_utilization() == pytest.approx(0.42)


def test_cpu_utilization_clamped_when_boosting() -> None:
    load = open_load(make_dll(cpu=106.5))
    load.refresh()
    assert load.cpu_utilization() == 1.0


def test_values_unknown_until_first_refresh() -> None:
    load = open_load(make_dll())
    assert load.cpu_utilization() is None
    assert load.gpu_utilizations() == {}


def test_gpu_utilizations_keep_only_discrete_adapters() -> None:
    load = open_load(make_dll(discrete=55.0, integrated=20.0))
    load.refresh()
    assert load.gpu_utilizations() == pytest.approx({DISCRETE: 0.55})


def test_gpu_utilizations_empty_without_discrete_adapters() -> None:
    load = open_load(make_dll(), discrete=frozenset())
    load.refresh()
    assert load.gpu_utilizations() == {}


def test_keeps_every_adapter_when_types_unknown(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        load = open_load(make_dll(discrete=55.0, integrated=20.0), discrete=None)
    load.refresh()
    assert load.gpu_utilizations() == pytest.approx({DISCRETE: 0.55, INTEGRATED: 0.20})
    assert len(caplog.records) == 1


def test_refresh_failure_keeps_last_values_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dll = make_dll(cpu=30.0, discrete=60.0)
    load = open_load(dll)
    load.refresh()
    dll.collect_status = 0xC0000BC6
    with caplog.at_level(logging.WARNING):
        load.refresh()
        load.refresh()
    assert load.cpu_utilization() == pytest.approx(0.30)
    assert load.gpu_utilizations() == pytest.approx({DISCRETE: 0.60})
    assert len(caplog.records) == 1


def test_refresh_picks_up_new_values() -> None:
    dll = make_dll(cpu=10.0)
    load = open_load(dll)
    load.refresh()
    dll.counters[CPU_UTILITY]["_Total"] = 80.0
    load.refresh()
    assert load.cpu_utilization() == pytest.approx(0.80)


def test_open_without_gpu_counter_still_reports_cpu() -> None:
    dll = FakePdh({CPU_UTILITY: {"_Total": 25.0}})
    load = open_load(dll)
    load.refresh()
    assert load.cpu_utilization() == pytest.approx(0.25)
    assert load.gpu_utilizations() == {}


def test_open_fails_without_any_counter() -> None:
    dll = FakePdh({})
    assert not PdhLoad(lambda: PdhQuery(dll), discrete_gpus=lambda: None).open()
    assert "PdhCloseQuery" in dll.calls


def test_open_fails_when_pdh_cannot_load() -> None:
    def missing_pdh() -> PdhQuery:
        raise OSError("pdh.dll not found")

    load = PdhLoad(missing_pdh, discrete_gpus=lambda: None)
    assert not load.open()
    load.refresh()
    assert load.cpu_utilization() is None


def test_close_releases_query() -> None:
    dll = make_dll()
    load = open_load(dll)
    load.close()
    load.refresh()
    assert dll.calls.count("PdhCloseQuery") == 1
