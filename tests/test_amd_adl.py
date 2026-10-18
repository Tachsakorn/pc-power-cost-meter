import logging
from typing import Any

import pytest

from power_meter.sensors.amd_adl import (
    ADL_POWER_SENSOR,
    AdapterRow,
    AdlGpu,
    discrete_adapter_indices,
)

ADL_ERR_NOT_SUPPORTED = -8
DISCRETE = 0b01
INTEGRATED = 0b10


class FakeAdl:
    """Stands in for atiadlxx.dll. `buses` maps each logical adapter index to its PCI bus;
    `watts` holds board power per adapter index, and an adapter missing from it has no sensor."""

    def __init__(
        self, buses: dict[int, int], watts: dict[int, int], asic: dict[int, int] | None = None
    ) -> None:
        self.buses = buses
        self.watts = watts
        self.asic = asic or {}
        self.valid_bits = 0xFF
        self.create_status = 0
        self.query_status = 0
        self.allocated = 0
        self.destroyed = 0
        self.queried: list[int] = []

    def ADL2_Main_Control_Create(self, malloc: Any, enum_connected: int, context: Any) -> int:
        self.allocated = malloc(64)
        context.contents.value = 0xADC
        return self.create_status

    def ADL2_Main_Control_Destroy(self, context: Any) -> int:
        self.destroyed += 1
        return 0

    def ADL2_Adapter_NumberOfAdapters_Get(self, context: Any, count: Any) -> int:
        count.contents.value = len(self.buses)
        return 0

    def ADL2_Adapter_AdapterInfo_Get(self, context: Any, infos: Any, size: int) -> int:
        for info, (index, bus) in zip(infos, self.buses.items(), strict=True):
            info.iAdapterIndex = index
            info.iVendorID = 1002
            info.iBusNumber = bus
        return 0

    def ADL2_Adapter_ASICFamilyType_Get(
        self, context: Any, index: int, types: Any, valids: Any
    ) -> int:
        types.contents.value = self.asic.get(index, DISCRETE)
        valids.contents.value = self.valid_bits
        return 0

    def ADL2_New_QueryPMLogData_Get(self, context: Any, index: int, log: Any) -> int:
        self.queried.append(index)
        if index in self.watts:
            sensor = log.contents.sensors[ADL_POWER_SENSOR]
            sensor.supported = 1
            sensor.value = self.watts[index]
        return self.query_status


def rows(*specs: tuple[int, int, int]) -> list[AdapterRow]:
    return [AdapterRow(index, vendor, bus, 0, 0) for index, vendor, bus in specs]


def always_discrete(index: int) -> int:
    return DISCRETE


def rx7600() -> FakeAdl:
    return FakeAdl(buses={index: 12 for index in range(5)}, watts={0: 132})


def test_vendor_id_decimal_and_hex_accepted() -> None:
    adapters = rows((0, 1002, 3), (1, 0x1002, 12), (2, 0x10DE, 1))
    assert discrete_adapter_indices(adapters, always_discrete) == [0, 1]


def test_duplicate_logical_adapters_collapsed() -> None:
    adapters = rows(*((index, 1002, 12) for index in range(5)))
    assert discrete_adapter_indices(adapters, always_discrete) == [0]


def test_integrated_adapters_are_skipped() -> None:
    adapters = rows((0, 1002, 0), (3, 1002, 12))
    asic = {0: INTEGRATED, 3: DISCRETE}
    assert discrete_adapter_indices(adapters, asic.__getitem__) == [3]


def test_asic_type_failure_skips_adapter() -> None:
    adapters = rows((0, 1002, 0), (3, 1002, 12))
    asic = {0: None, 3: DISCRETE}
    assert discrete_adapter_indices(adapters, asic.__getitem__) == [3]


def test_asic_bits_outside_the_valid_mask_are_ignored() -> None:
    dll = rx7600()
    dll.valid_bits = INTEGRATED
    assert not AdlGpu(dll).open()


def test_missing_power_sensor_reports_unavailable() -> None:
    dll = FakeAdl(buses={0: 12}, watts={})
    assert not AdlGpu(dll).open()
    assert dll.destroyed == 1


def test_reads_board_power_of_one_physical_gpu() -> None:
    dll = rx7600()
    gpu = AdlGpu(dll)
    assert gpu.open()
    assert dll.allocated
    dll.queried.clear()
    reading = gpu.read()
    assert reading is not None
    assert reading.watts == 132.0
    assert reading.source == "AMD ADL"
    assert reading.measured
    assert gpu.component == "gpu"
    assert dll.queried == [0]


def test_read_sums_discrete_power() -> None:
    dll = FakeAdl(
        buses={0: 0, 1: 3, 2: 3, 3: 12},
        watts={0: 15, 1: 140, 3: 7},
        asic={0: INTEGRATED},
    )
    gpu = AdlGpu(dll)
    assert gpu.open()
    reading = gpu.read()
    assert reading is not None
    assert reading.watts == 147.0


def test_read_none_when_a_query_fails_and_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    dll = rx7600()
    gpu = AdlGpu(dll)
    assert gpu.open()
    dll.query_status = ADL_ERR_NOT_SUPPORTED
    with caplog.at_level(logging.WARNING):
        assert gpu.read() is None
        assert gpu.read() is None
    assert len(caplog.records) == 1
    assert "-8" in caplog.text


def test_unavailable_when_context_creation_fails() -> None:
    dll = rx7600()
    dll.create_status = ADL_ERR_NOT_SUPPORTED
    assert not AdlGpu(dll).open()
    assert dll.destroyed == 0


def test_unavailable_when_adl_cannot_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert not AdlGpu().open()


def test_close_destroys_context_once() -> None:
    dll = rx7600()
    gpu = AdlGpu(dll)
    assert gpu.open()
    gpu.close()
    gpu.close()
    assert dll.destroyed == 1
    assert gpu.read() is None


def test_reopens_after_close() -> None:
    dll = rx7600()
    gpu = AdlGpu(dll)
    assert gpu.open()
    gpu.close()
    assert gpu.open()
    assert gpu.read() is not None
