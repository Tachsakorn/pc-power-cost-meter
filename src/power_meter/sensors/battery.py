"""Laptop battery charge and discharge rate through SetupAPI and IOCTL_BATTERY_* requests.

Both work for a standard user; no admin rights are needed.
"""

import ctypes
import itertools
import logging
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from power_meter.sensors.base import BatteryReading
from power_meter.sensors.warn_once import WarnOnce

IOCTL_BATTERY_QUERY_TAG: Final = 0x294040
IOCTL_BATTERY_QUERY_INFORMATION: Final = 0x294044
IOCTL_BATTERY_QUERY_STATUS: Final = 0x29404C
BATTERY_SYSTEM_BATTERY: Final = 0x80000000
BATTERY_CAPACITY_RELATIVE: Final = 0x40000000
BATTERY_UNKNOWN_RATE: Final = -0x80000000
AC_LINE_ONLINE: Final = 1
NO_SYSTEM_BATTERY: Final = 128
BATTERY_STATUS_UNKNOWN: Final = 255

_BATTERY_INFORMATION_LEVEL = 0
_DIGCF_PRESENT = 0x02
_DIGCF_DEVICEINTERFACE = 0x10
_GENERIC_READ_WRITE = 0xC0000000
_FILE_SHARE_READ_WRITE = 0x03
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_INVALID_HANDLE = ctypes.c_void_p(-1).value

logger = logging.getLogger(__name__)

_Guid = ctypes.c_ubyte * 16
_GUID_DEVCLASS_BATTERY = _Guid.from_buffer_copy(
    uuid.UUID("72631e54-78a4-11d0-bcf7-00aa00b7b32a").bytes_le
)


class _DeviceInterfaceData(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("InterfaceClassGuid", _Guid),
        ("Flags", ctypes.c_uint32),
        ("Reserved", ctypes.c_size_t),
    ]


class _BatteryQueryInformation(ctypes.Structure):
    _fields_ = [
        ("BatteryTag", ctypes.c_uint32),
        ("InformationLevel", ctypes.c_int),
        ("AtRate", ctypes.c_int32),
    ]


class _BatteryInformation(ctypes.Structure):
    _fields_ = [
        ("Capabilities", ctypes.c_uint32),
        ("Technology", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte * 3),
        ("Chemistry", ctypes.c_ubyte * 4),
        ("DesignedCapacity", ctypes.c_uint32),
        ("FullChargedCapacity", ctypes.c_uint32),
        ("DefaultAlert1", ctypes.c_uint32),
        ("DefaultAlert2", ctypes.c_uint32),
        ("CriticalBias", ctypes.c_uint32),
        ("CycleCount", ctypes.c_uint32),
    ]


class _BatteryWaitStatus(ctypes.Structure):
    _fields_ = [
        ("BatteryTag", ctypes.c_uint32),
        ("Timeout", ctypes.c_uint32),
        ("PowerState", ctypes.c_uint32),
        ("LowCapacity", ctypes.c_uint32),
        ("HighCapacity", ctypes.c_uint32),
    ]


class _BatteryStatus(ctypes.Structure):
    _fields_ = [
        ("PowerState", ctypes.c_uint32),
        ("Capacity", ctypes.c_uint32),
        ("Voltage", ctypes.c_uint32),
        ("Rate", ctypes.c_int32),
    ]


class _SystemPowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_uint32),
        ("BatteryFullLifeTime", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class _Battery:
    handle: int
    tag: int
    relative: bool


def _last_error() -> int:
    if sys.platform == "win32":
        return ctypes.get_last_error()
    return 0


def _discharge_watts(rates_mw: Sequence[int]) -> float | None:
    """Net discharge in watts; None while charging or idle, or when a rate is unknown."""
    if BATTERY_UNKNOWN_RATE in rates_mw:
        return None
    total = sum(rates_mw)
    # The driver reports milliwatts, negative while discharging.
    return -total / 1000 if total < 0 else None


def _load_kernel32() -> Any:
    if sys.platform != "win32":
        raise OSError("kernel32 is only available on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    void, dword = ctypes.c_void_p, ctypes.c_uint32
    signatures: dict[str, tuple[Any, list[Any]]] = {
        "GetSystemPowerStatus": (ctypes.c_int, [ctypes.POINTER(_SystemPowerStatus)]),
        "CreateFileW": (void, [ctypes.c_wchar_p, dword, dword, void, dword, dword, void]),
        "DeviceIoControl": (
            ctypes.c_int,
            [void, dword, void, dword, void, dword, ctypes.POINTER(dword), void],
        ),
        "CloseHandle": (ctypes.c_int, [void]),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(kernel32, name)
        function.restype = restype
        function.argtypes = argtypes
    return kernel32


def _load_setupapi() -> Any:
    if sys.platform != "win32":
        raise OSError("setupapi is only available on Windows")
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    void, dword = ctypes.c_void_p, ctypes.c_uint32
    interface = ctypes.POINTER(_DeviceInterfaceData)
    guid = ctypes.POINTER(_Guid)
    signatures: dict[str, tuple[Any, list[Any]]] = {
        "SetupDiGetClassDevsW": (void, [guid, ctypes.c_wchar_p, void, dword]),
        "SetupDiEnumDeviceInterfaces": (ctypes.c_int, [void, void, guid, dword, interface]),
        "SetupDiGetDeviceInterfaceDetailW": (
            ctypes.c_int,
            [void, interface, void, dword, ctypes.POINTER(dword), void],
        ),
        "SetupDiDestroyDeviceInfoList": (ctypes.c_int, [void]),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(setupapi, name)
        function.restype = restype
        function.argtypes = argtypes
    return setupapi


def _battery_paths(setupapi: Any) -> list[str]:
    battery_class = ctypes.pointer(_GUID_DEVCLASS_BATTERY)
    devices = setupapi.SetupDiGetClassDevsW(
        battery_class, None, None, _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE
    )
    if devices in (None, _INVALID_HANDLE):
        raise OSError(f"SetupDiGetClassDevsW failed with error {_last_error()}")
    paths = []
    try:
        for index in itertools.count():
            interface = _DeviceInterfaceData(cbSize=ctypes.sizeof(_DeviceInterfaceData))
            if not setupapi.SetupDiEnumDeviceInterfaces(
                devices, None, battery_class, index, ctypes.pointer(interface)
            ):
                break
            paths.append(_device_path(setupapi, devices, interface))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(devices)
    return paths


def _device_path(setupapi: Any, devices: int, interface: _DeviceInterfaceData) -> str:
    required = ctypes.c_uint32()
    setupapi.SetupDiGetDeviceInterfaceDetailW(
        devices, ctypes.pointer(interface), None, 0, ctypes.pointer(required), None
    )
    detail = ctypes.create_string_buffer(required.value)
    # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize is the size of the fixed part including padding,
    # not of the buffer: 8 on 64-bit, 6 on 32-bit. DevicePath starts after the 4-byte cbSize.
    ctypes.cast(detail, ctypes.POINTER(ctypes.c_uint32))[0] = (
        8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
    )
    if not setupapi.SetupDiGetDeviceInterfaceDetailW(
        devices, ctypes.pointer(interface), detail, required.value, None, None
    ):
        raise OSError(f"SetupDiGetDeviceInterfaceDetailW failed with error {_last_error()}")
    return ctypes.wstring_at(ctypes.addressof(detail) + 4)


def battery_present(kernel32: object | None = None) -> bool:
    """Whether Windows reports a system battery, which marks this PC as a laptop."""
    try:
        dll: Any = kernel32 if kernel32 is not None else _load_kernel32()
    except (OSError, AttributeError) as exc:
        logger.warning("Can't query the system power status: %s", exc)
        return False
    status = _SystemPowerStatus()
    if not dll.GetSystemPowerStatus(ctypes.pointer(status)):
        logger.warning("GetSystemPowerStatus failed with error %d", _last_error())
        return False
    return status.BatteryFlag not in (NO_SYSTEM_BATTERY, BATTERY_STATUS_UNKNOWN)


class WindowsBattery:
    """Net charge/discharge rate of the system batteries and whether the PC runs on AC."""

    def __init__(self, kernel32: object | None = None, setupapi: object | None = None) -> None:
        self._kernel32: Any = kernel32
        self._setupapi: Any = setupapi
        self._batteries: list[_Battery] = []
        self._warn = WarnOnce(logger)

    def open(self) -> bool:
        try:
            if self._kernel32 is None:
                self._kernel32 = _load_kernel32()
            if self._setupapi is None:
                self._setupapi = _load_setupapi()
            paths = _battery_paths(self._setupapi)
        except (OSError, AttributeError) as exc:
            self._warn("Battery status unavailable: %s", exc)
            return False
        for number, path in enumerate(paths, start=1):
            battery = self._open_battery(path, number)
            if battery is not None:
                self._batteries.append(battery)
        if not self._batteries:
            self._warn("No system battery reports its status")
            return False
        return True

    def read(self) -> BatteryReading | None:
        if not self._batteries:
            return None
        power = _SystemPowerStatus()
        if not self._kernel32.GetSystemPowerStatus(ctypes.pointer(power)):
            self._warn("GetSystemPowerStatus failed with error %d", _last_error())
            return None
        rates = []
        try:
            for battery in self._batteries:
                status = _BatteryStatus()
                self._ioctl(
                    battery.handle,
                    IOCTL_BATTERY_QUERY_STATUS,
                    _BatteryWaitStatus(BatteryTag=battery.tag),
                    status,
                )
                rates.append(status.Rate)
        except OSError as exc:
            self._warn("Battery status read failed: %s", exc)
            return None
        # With relative capacity the rate isn't in milliwatts, so it can't be used.
        relative = any(battery.relative for battery in self._batteries)
        return BatteryReading(
            on_ac=power.ACLineStatus == AC_LINE_ONLINE,
            rate_w=None if relative else _discharge_watts(rates),
        )

    def close(self) -> None:
        for battery in self._batteries:
            self._kernel32.CloseHandle(battery.handle)
        self._batteries = []

    def _open_battery(self, path: str, number: int) -> _Battery | None:
        # Logged by number: a device path holds the hardware instance ID, which can include a
        # serial number.
        handle = self._kernel32.CreateFileW(
            path,
            _GENERIC_READ_WRITE,
            _FILE_SHARE_READ_WRITE,
            None,
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if handle in (None, _INVALID_HANDLE):
            self._warn("Can't open battery %d: error %d", number, _last_error())
            return None
        try:
            tag = ctypes.c_uint32()
            self._ioctl(handle, IOCTL_BATTERY_QUERY_TAG, ctypes.c_uint32(0), tag)
            if tag.value == 0:
                raise OSError("no battery in this slot")
            info = _BatteryInformation()
            query = _BatteryQueryInformation(
                BatteryTag=tag.value, InformationLevel=_BATTERY_INFORMATION_LEVEL
            )
            self._ioctl(handle, IOCTL_BATTERY_QUERY_INFORMATION, query, info)
            # UPS units also enumerate as batteries but don't power the PC directly.
            if not info.Capabilities & BATTERY_SYSTEM_BATTERY:
                raise OSError("not a system battery")
        except OSError as exc:
            self._kernel32.CloseHandle(handle)
            self._warn("Skipping battery %d: %s", number, exc)
            return None
        return _Battery(handle, tag.value, bool(info.Capabilities & BATTERY_CAPACITY_RELATIVE))

    def _ioctl(
        self,
        handle: int,
        code: int,
        request: ctypes.Structure | ctypes.c_uint32,
        reply: ctypes.Structure | ctypes.c_uint32,
    ) -> None:
        returned = ctypes.c_uint32()
        if not self._kernel32.DeviceIoControl(
            handle,
            code,
            ctypes.pointer(request),
            ctypes.sizeof(request),
            ctypes.pointer(reply),
            ctypes.sizeof(reply),
            ctypes.pointer(returned),
            None,
        ):
            raise OSError(f"DeviceIoControl 0x{code:X} failed with error {_last_error()}")
