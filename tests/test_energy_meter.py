import logging

import pytest
from fakes import PDH_CALC_NEGATIVE_DENOMINATOR, PDH_INVALID_HANDLE, PDH_NO_DATA, FakePdh

from power_meter.sensors.energy_meter import (
    ENERGY_METER_POWER,
    LAST_READING_MAX_AGE_S,
    EnergyMeterCpu,
)
from power_meter.sensors.pdh import PdhQuery

PROBE = {"rapl_package0_pkg": 38470.6, "rapl_package0_core0_core": 688.8, "_total": 0.0}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def meter_for(dll: FakePdh, clock: FakeClock | None = None) -> EnergyMeterCpu:
    return EnergyMeterCpu(lambda: PdhQuery(dll), clock=clock or FakeClock())


def test_reads_package_watts() -> None:
    dll = FakePdh({ENERGY_METER_POWER: dict(PROBE)})
    meter = meter_for(dll)
    assert meter.open()
    dll.counters[ENERGY_METER_POWER]["rapl_package0_pkg"] = 76600.0
    reading = meter.read()
    assert reading is not None
    assert reading.watts == pytest.approx(76.6)
    assert reading.source == "Energy Meter (RAPL)"
    assert reading.measured
    assert meter.component == "cpu"


def test_open_collects_twice_for_rate_counters() -> None:
    dll = FakePdh({ENERGY_METER_POWER: dict(PROBE)})
    assert meter_for(dll).open()
    assert dll.calls.count("PdhCollectQueryData") == 2


def test_energy_meter_unavailable_when_add_fails() -> None:
    dll = FakePdh({})
    assert not meter_for(dll).open()
    assert "PdhCloseQuery" in dll.calls


def test_unavailable_without_package_instance() -> None:
    dll = FakePdh({ENERGY_METER_POWER: {"_total": 0.0}})
    assert not meter_for(dll).open()
    assert "PdhCloseQuery" in dll.calls


def test_unavailable_when_pdh_cannot_load() -> None:
    def missing_pdh() -> PdhQuery:
        raise OSError("pdh.dll not found")

    assert not EnergyMeterCpu(missing_pdh).open()


def test_read_failure_returns_none_and_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    dll = FakePdh({ENERGY_METER_POWER: dict(PROBE)})
    meter = meter_for(dll)
    assert meter.open()
    dll.collect_status = PDH_INVALID_HANDLE
    with caplog.at_level(logging.WARNING):
        assert meter.read() is None
        assert meter.read() is None
    assert len(caplog.records) == 1
    assert "0xC0000BBC" in caplog.text


def test_no_data_reuses_recent_reading(caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    dll = FakePdh({ENERGY_METER_POWER: dict(PROBE)})
    meter = meter_for(dll, clock)
    assert meter.open()
    dll.counters[ENERGY_METER_POWER]["rapl_package0_pkg"] = 7000.0
    good = meter.read()
    dll.array_status[ENERGY_METER_POWER] = PDH_CALC_NEGATIVE_DENOMINATOR
    clock.now = LAST_READING_MAX_AGE_S
    with caplog.at_level(logging.WARNING):
        assert meter.read() == good
    assert caplog.records == []


def test_no_data_gives_none_once_last_reading_is_too_old() -> None:
    clock = FakeClock()
    dll = FakePdh({ENERGY_METER_POWER: dict(PROBE)})
    meter = meter_for(dll, clock)
    assert meter.open()
    assert meter.read() is not None
    dll.collect_status = PDH_NO_DATA
    for second in range(1, int(LAST_READING_MAX_AGE_S) + 1):
        clock.now = float(second)
        assert meter.read() is not None
    clock.now = LAST_READING_MAX_AGE_S + 0.5
    assert meter.read() is None


def test_no_data_right_after_open_reuses_the_open_reading() -> None:
    dll = FakePdh({ENERGY_METER_POWER: dict(PROBE)})
    meter = meter_for(dll)
    assert meter.open()
    dll.collect_status = PDH_NO_DATA
    reading = meter.read()
    assert reading is not None
    assert reading.watts == pytest.approx(38.4706)


def test_read_none_when_package_instance_disappears() -> None:
    dll = FakePdh({ENERGY_METER_POWER: dict(PROBE)})
    meter = meter_for(dll)
    assert meter.open()
    dll.counters[ENERGY_METER_POWER] = {"_total": 0.0}
    assert meter.read() is None


def test_close_releases_query() -> None:
    dll = FakePdh({ENERGY_METER_POWER: dict(PROBE)})
    meter = meter_for(dll)
    assert meter.open()
    meter.close()
    assert dll.calls.count("PdhCloseQuery") == 1
    assert meter.read() is None
