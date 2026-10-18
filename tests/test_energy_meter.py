import logging

import pytest
from fakes import FakePdh

from power_meter.sensors.energy_meter import ENERGY_METER_POWER, EnergyMeterCpu
from power_meter.sensors.pdh import PdhQuery

PROBE = {"rapl_package0_pkg": 38470.6, "rapl_package0_core0_core": 688.8, "_total": 0.0}


def meter_for(dll: FakePdh) -> EnergyMeterCpu:
    return EnergyMeterCpu(lambda: PdhQuery(dll))


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
    dll.collect_status = 0xC0000BC6
    with caplog.at_level(logging.WARNING):
        assert meter.read() is None
        assert meter.read() is None
    assert len(caplog.records) == 1
    assert "0xC0000BC6" in caplog.text


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
