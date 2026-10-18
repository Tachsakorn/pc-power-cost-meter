"""Board power of discrete AMD Radeon GPUs from the AMD Display Library (atiadlxx.dll)."""

import ctypes
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from power_meter.sensors.base import Component, Reading
from power_meter.sensors.warn_once import WarnOnce

ADL_OK: Final = 0
ADL_ASIC_DISCRETE: Final = 1 << 0
ADL_POWER_SENSOR: Final = 73  # PMLOG_BOARD_POWER: total board power in watts
# ADL reports AMD's PCI vendor id as the decimal number 1002 rather than 0x1002.
AMD_VENDOR_IDS: Final = frozenset({1002, 0x1002})

logger = logging.getLogger(__name__)


class _AdapterInfo(ctypes.Structure):
    _fields_ = [
        ("iSize", ctypes.c_int),
        ("iAdapterIndex", ctypes.c_int),
        ("strUDID", ctypes.c_char * 256),
        ("iBusNumber", ctypes.c_int),
        ("iDeviceNumber", ctypes.c_int),
        ("iFunctionNumber", ctypes.c_int),
        ("iVendorID", ctypes.c_int),
        ("strAdapterName", ctypes.c_char * 256),
        ("strDisplayName", ctypes.c_char * 256),
        ("iPresent", ctypes.c_int),
        ("iExist", ctypes.c_int),
        ("strDriverPath", ctypes.c_char * 256),
        ("strDriverPathExt", ctypes.c_char * 256),
        ("strPNPString", ctypes.c_char * 256),
        ("iOSDisplayIndex", ctypes.c_int),
    ]


class _SensorData(ctypes.Structure):
    _fields_ = [("supported", ctypes.c_int), ("value", ctypes.c_int)]


class _PMLogDataOutput(ctypes.Structure):
    _fields_ = [("size", ctypes.c_int), ("sensors", _SensorData * 256)]


# The SDK declares the allocator __stdcall; CFUNCTYPE only lets the module import off Windows.
if sys.platform == "win32":
    _Malloc = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_int)
else:
    _Malloc = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)


@dataclass(frozen=True)
class AdapterRow:
    index: int
    vendor_id: int
    bus: int
    device: int
    function: int


def discrete_adapter_indices(
    adapters: Sequence[AdapterRow], asic_type: Callable[[int], int | None]
) -> list[int]:
    """One ADL adapter index per physical discrete AMD GPU.

    ADL lists a logical adapter per display output, so one card shows up several times at the
    same PCI location. `asic_type` returns the ADL_ASIC_* bits, or None when the query fails.
    """
    seen: set[tuple[int, int, int]] = set()
    indices = []
    for adapter in adapters:
        location = (adapter.bus, adapter.device, adapter.function)
        if adapter.vendor_id not in AMD_VENDOR_IDS or location in seen:
            continue
        seen.add(location)
        asic = asic_type(adapter.index)
        if asic is not None and asic & ADL_ASIC_DISCRETE:
            indices.append(adapter.index)
    return indices


def _load_adl() -> Any:
    if sys.platform != "win32":
        raise OSError("atiadlxx.dll is only available on Windows")
    adl = ctypes.CDLL("atiadlxx.dll")
    int_out = ctypes.POINTER(ctypes.c_int)
    signatures: dict[str, list[Any]] = {
        "ADL2_Main_Control_Create": [_Malloc, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
        "ADL2_Main_Control_Destroy": [ctypes.c_void_p],
        "ADL2_Adapter_NumberOfAdapters_Get": [ctypes.c_void_p, int_out],
        "ADL2_Adapter_AdapterInfo_Get": [
            ctypes.c_void_p,
            ctypes.POINTER(_AdapterInfo),
            ctypes.c_int,
        ],
        "ADL2_Adapter_ASICFamilyType_Get": [ctypes.c_void_p, ctypes.c_int, int_out, int_out],
        "ADL2_New_QueryPMLogData_Get": [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(_PMLogDataOutput),
        ],
    }
    for name, argtypes in signatures.items():
        function = getattr(adl, name)
        function.argtypes = argtypes
        function.restype = ctypes.c_int
    return adl


class AdlGpu:
    """Measured board power summed over every discrete AMD GPU."""

    component: Component = "gpu"

    def __init__(self, dll: object | None = None) -> None:
        self._dll: Any = dll
        self._context: ctypes.c_void_p | None = None
        self._malloc: Any = None
        self._buffers: list[ctypes.Array[ctypes.c_char]] = []
        self._adapters: list[int] = []
        self._warn = WarnOnce(logger)

    def open(self) -> bool:
        if self._dll is None:
            try:
                self._dll = _load_adl()
            except (OSError, AttributeError) as exc:
                # The packaged exe re-raises a failed DLL load with a hint about missing bundled
                # files, which is wrong for a driver DLL; the Windows error is the useful part.
                self._warn("AMD ADL unavailable: %s", exc.__cause__ or exc)
                return False
        # ADL calls back into this allocator for as long as the context exists.
        malloc = _Malloc(self._allocate)
        context = ctypes.c_void_p()
        status = self._dll.ADL2_Main_Control_Create(malloc, 1, ctypes.pointer(context))
        if status != ADL_OK:
            self._warn("ADL2_Main_Control_Create failed with status %d", status)
            return False
        self._malloc = malloc
        self._context = context
        discrete = discrete_adapter_indices(self._adapter_rows(), self._asic_type)
        self._adapters = [index for index in discrete if self._has_power_sensor(index)]
        if not self._adapters:
            self._warn("No discrete AMD GPU reports board power through ADL")
            self.close()
            return False
        return True

    def read(self) -> Reading | None:
        if self._context is None:
            return None
        watts = 0
        for index in self._adapters:
            log = self._query(index)
            if log is None:
                return None
            watts += log.sensors[ADL_POWER_SENSOR].value
        return Reading(float(watts), "AMD ADL", measured=True)

    def close(self) -> None:
        if self._context is not None:
            self._dll.ADL2_Main_Control_Destroy(self._context)
            self._context = None
        self._malloc = None
        self._buffers.clear()
        self._adapters = []

    def _allocate(self, size: int) -> int:
        buffer = ctypes.create_string_buffer(size)
        self._buffers.append(buffer)
        return ctypes.addressof(buffer)

    def _adapter_rows(self) -> list[AdapterRow]:
        count = ctypes.c_int()
        status = self._dll.ADL2_Adapter_NumberOfAdapters_Get(self._context, ctypes.pointer(count))
        if status != ADL_OK or count.value <= 0:
            self._warn("ADL2_Adapter_NumberOfAdapters_Get returned status %d", status)
            return []
        infos = (_AdapterInfo * count.value)()
        status = self._dll.ADL2_Adapter_AdapterInfo_Get(self._context, infos, ctypes.sizeof(infos))
        if status != ADL_OK:
            self._warn("ADL2_Adapter_AdapterInfo_Get failed with status %d", status)
            return []
        return [
            AdapterRow(
                info.iAdapterIndex,
                info.iVendorID,
                info.iBusNumber,
                info.iDeviceNumber,
                info.iFunctionNumber,
            )
            for info in infos
        ]

    def _asic_type(self, index: int) -> int | None:
        types = ctypes.c_int()
        valids = ctypes.c_int()
        status = self._dll.ADL2_Adapter_ASICFamilyType_Get(
            self._context, index, ctypes.pointer(types), ctypes.pointer(valids)
        )
        if status != ADL_OK:
            self._warn("ADL2_Adapter_ASICFamilyType_Get failed for adapter %d: %d", index, status)
            return None
        # Only the bits flagged in the second output carry meaning.
        return types.value & valids.value

    def _has_power_sensor(self, index: int) -> bool:
        log = self._query(index)
        return log is not None and bool(log.sensors[ADL_POWER_SENSOR].supported)

    def _query(self, index: int) -> _PMLogDataOutput | None:
        log = _PMLogDataOutput()
        log.size = ctypes.sizeof(log)
        status = self._dll.ADL2_New_QueryPMLogData_Get(self._context, index, ctypes.pointer(log))
        if status != ADL_OK:
            self._warn("ADL2_New_QueryPMLogData_Get failed for adapter %d: %d", index, status)
            return None
        return log
