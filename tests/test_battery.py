import ctypes
import logging
from typing import Any

import pytest

from power_meter.sensors.battery import (
    BATTERY_CAPACITY_RELATIVE,
    BATTERY_SYSTEM_BATTERY,
    BATTERY_UNKNOWN_RATE,
    IOCTL_BATTERY_QUERY_INFORMATION,
    IOCTL_BATTERY_QUERY_STATUS,
    IOCTL_BATTERY_QUERY_TAG,
    WindowsBattery,
    battery_present,
)

INVALID_HANDLE = ctypes.c_void_p(-1).value
CHARGING = 8


class FakeSetupApi:
    """Stands in for setupapi.dll, listing one battery interface per path."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = paths
        self.destroyed = 0

    def SetupDiGetClassDevsW(self, guid: Any, enumerator: None, parent: None, flags: int) -> int:
        return 0xD1

    def SetupDiEnumDeviceInterfaces(
        self, devices: int, device: None, guid: Any, index: int, interface: Any
    ) -> int:
        if index >= len(self.paths):
            return 0
        interface.contents.Reserved = index
        return 1

    def SetupDiGetDeviceInterfaceDetailW(
        self, devices: int, interface: Any, detail: Any, size: int, required: Any, device: None
    ) -> int:
        path = self.paths[interface.contents.Reserved]
        text_size = ctypes.sizeof(ctypes.c_wchar) * (len(path) + 1)
        if detail is None:
            required.contents.value = 4 + text_size
            return 0
        ctypes.memmove(ctypes.addressof(detail) + 4, ctypes.create_unicode_buffer(path), text_size)
        return 1

    def SetupDiDestroyDeviceInfoList(self, devices: int) -> int:
        self.destroyed += 1
        return 1


class FakeKernel32:
    """Stands in for kernel32.dll. Battery handles are 100 + the battery's index."""

    def __init__(self, rates_mw: list[int], capabilities: int = BATTERY_SYSTEM_BATTERY) -> None:
        self.rates_mw = rates_mw
        self.capabilities = capabilities
        self.ac_line = 0
        self.battery_flag = 0
        self.tag = 7
        self.status_ok = True
        self.opened: list[str] = []
        self.closed: list[int] = []

    def GetSystemPowerStatus(self, status: Any) -> int:
        status.contents.ACLineStatus = self.ac_line
        status.contents.BatteryFlag = self.battery_flag
        return 1

    def CreateFileW(self, path: str, *args: object) -> int:
        self.opened.append(path)
        return 100 + int(path.rsplit("#", 1)[1])

    def DeviceIoControl(
        self,
        handle: int,
        code: int,
        request: Any,
        request_size: int,
        reply: Any,
        reply_size: int,
        returned: Any,
        overlapped: None,
    ) -> int:
        if code == IOCTL_BATTERY_QUERY_TAG:
            reply.contents.value = self.tag
        elif code == IOCTL_BATTERY_QUERY_INFORMATION:
            assert request.contents.BatteryTag == self.tag
            reply.contents.Capabilities = self.capabilities
        elif code == IOCTL_BATTERY_QUERY_STATUS:
            if not self.status_ok:
                return 0
            reply.contents.Rate = self.rates_mw[handle - 100]
        return 1

    def CloseHandle(self, handle: int) -> int:
        self.closed.append(handle)
        return 1


def laptop(
    *rates_mw: int, capabilities: int = BATTERY_SYSTEM_BATTERY
) -> tuple[FakeKernel32, FakeSetupApi]:
    paths = [rf"\\?\acpi#pnp0c0a#{index}" for index in range(len(rates_mw))]
    return FakeKernel32(list(rates_mw), capabilities), FakeSetupApi(paths)


def read_rate(kernel32: FakeKernel32, setupapi: FakeSetupApi) -> float | None:
    battery = WindowsBattery(kernel32, setupapi)
    assert battery.open()
    reading = battery.read()
    assert reading is not None
    return reading.rate_w


def test_battery_rate_conversion() -> None:
    kernel32, setupapi = laptop(-15000)
    battery = WindowsBattery(kernel32, setupapi)
    assert battery.open()
    reading = battery.read()
    assert reading is not None
    assert reading.rate_w == 15.0
    assert not reading.on_ac


def test_battery_unknown_rate_is_none() -> None:
    assert read_rate(*laptop(BATTERY_UNKNOWN_RATE)) is None


def test_battery_relative_capacity_is_none() -> None:
    assert (
        read_rate(*laptop(-15000, capabilities=BATTERY_SYSTEM_BATTERY | BATTERY_CAPACITY_RELATIVE))
        is None
    )


def test_battery_charging_rate_is_none_and_on_ac_reported() -> None:
    kernel32, setupapi = laptop(20000)
    kernel32.ac_line = 1
    battery = WindowsBattery(kernel32, setupapi)
    assert battery.open()
    reading = battery.read()
    assert reading is not None
    assert reading.on_ac
    assert reading.rate_w is None


def test_battery_rates_of_two_batteries_are_summed() -> None:
    assert read_rate(*laptop(-10000, -5500)) == 15.5


def test_battery_unknown_rate_on_either_battery_is_none() -> None:
    assert read_rate(*laptop(-10000, BATTERY_UNKNOWN_RATE)) is None


def test_battery_keeps_handles_open_between_reads() -> None:
    kernel32, setupapi = laptop(-10000, -5000)
    battery = WindowsBattery(kernel32, setupapi)
    assert battery.open()
    battery.read()
    battery.read()
    assert len(kernel32.opened) == 2
    assert kernel32.closed == []
    assert setupapi.destroyed == 1
    battery.close()
    battery.close()
    assert kernel32.closed == [100, 101]
    assert battery.read() is None


def test_battery_unavailable_without_battery_devices() -> None:
    assert not WindowsBattery(*laptop()).open()


def test_battery_without_tag_is_skipped() -> None:
    kernel32, setupapi = laptop(-15000)
    kernel32.tag = 0
    assert not WindowsBattery(kernel32, setupapi).open()
    assert kernel32.closed == [100]


def test_non_system_battery_is_skipped() -> None:
    kernel32, setupapi = laptop(-15000, capabilities=0)
    assert not WindowsBattery(kernel32, setupapi).open()
    assert kernel32.closed == [100]


def test_battery_that_cannot_be_opened_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel32, setupapi = laptop(-15000)
    monkeypatch.setattr(kernel32, "CreateFileW", lambda *args: INVALID_HANDLE)
    assert not WindowsBattery(kernel32, setupapi).open()


def test_battery_status_failure_returns_none_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    kernel32, setupapi = laptop(-15000)
    battery = WindowsBattery(kernel32, setupapi)
    assert battery.open()
    kernel32.status_ok = False
    with caplog.at_level(logging.WARNING):
        assert battery.read() is None
        assert battery.read() is None
    assert len(caplog.records) == 1


def test_battery_unavailable_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert not WindowsBattery().open()


@pytest.mark.parametrize(
    ("flag", "present"), [(0, True), (CHARGING, True), (128, False), (255, False)]
)
def test_battery_present_from_battery_flag(flag: int, present: bool) -> None:
    kernel32 = FakeKernel32([])
    kernel32.battery_flag = flag
    assert battery_present(kernel32) is present


def test_battery_present_false_when_power_status_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel32 = FakeKernel32([])
    monkeypatch.setattr(kernel32, "GetSystemPowerStatus", lambda status: 0)
    assert not battery_present(kernel32)


def test_battery_present_false_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert not battery_present()
