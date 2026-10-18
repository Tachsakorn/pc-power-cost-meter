"""Minimal ctypes binding for the Windows Performance Data Helper (pdh.dll)."""

import ctypes
import sys
from typing import Any, Final

PDH_FMT_DOUBLE: Final = 0x00000200
PDH_FMT_NOCAP100: Final = 0x00008000
PDH_MORE_DATA: Final = 0x800007D2
PDH_CSTATUS_VALID_DATA: Final = 0x00000000
PDH_CSTATUS_NEW_DATA: Final = 0x00000001
ERROR_SUCCESS: Final = 0


class CounterValue(ctypes.Structure):
    # PDH_FMT_COUNTERVALUE: the value is a union of 8-byte members; only doubles are requested.
    _fields_ = [("CStatus", ctypes.c_uint32), ("doubleValue", ctypes.c_double)]


class CounterValueItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", CounterValue)]


class PdhError(OSError):
    def __init__(self, function: str, status: int) -> None:
        super().__init__(f"{function} failed with status 0x{status:08X}")
        self.status = status


def _load_pdh() -> Any:
    if sys.platform != "win32":
        raise OSError("pdh.dll is only available on Windows")
    pdh = ctypes.WinDLL("pdh")
    handle_out = ctypes.POINTER(ctypes.c_void_p)
    dword_out = ctypes.POINTER(ctypes.c_uint32)
    signatures: dict[str, list[Any]] = {
        "PdhOpenQueryW": [ctypes.c_wchar_p, ctypes.c_size_t, handle_out],
        "PdhAddEnglishCounterW": [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_size_t, handle_out],
        "PdhCollectQueryData": [ctypes.c_void_p],
        "PdhGetFormattedCounterArrayW": [
            ctypes.c_void_p,
            ctypes.c_uint32,
            dword_out,
            dword_out,
            ctypes.c_void_p,
        ],
        "PdhCloseQuery": [ctypes.c_void_p],
    }
    for name, argtypes in signatures.items():
        function = getattr(pdh, name)
        function.argtypes = argtypes
        # PDH_STATUS is a LONG; read it unsigned so it compares with the documented hex codes.
        function.restype = ctypes.c_uint32
    return pdh


class PdhQuery:
    """One PDH query. Counter paths use English names, so they work on any display language."""

    def __init__(self, dll: object | None = None) -> None:
        self._dll: Any = dll if dll is not None else _load_pdh()
        query = ctypes.c_void_p()
        self._call("PdhOpenQueryW", None, 0, ctypes.pointer(query))
        self._query: ctypes.c_void_p | None = query

    def add_english(self, path: str) -> int:
        counter = ctypes.c_void_p()
        self._call("PdhAddEnglishCounterW", self._query, path, 0, ctypes.pointer(counter))
        return counter.value or 0

    def collect(self) -> None:
        self._call("PdhCollectQueryData", self._query)

    def values(self, counter: int) -> dict[str, float]:
        """Formatted value of every instance with valid data, keyed by instance name."""
        fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        size = ctypes.c_uint32(0)
        count = ctypes.c_uint32(0)
        get_array = self._dll.PdhGetFormattedCounterArrayW
        status = get_array(counter, fmt, ctypes.pointer(size), ctypes.pointer(count), None)
        if status == ERROR_SUCCESS:
            return {}
        if status != PDH_MORE_DATA:
            raise PdhError("PdhGetFormattedCounterArrayW", status)

        # Instance names are stored in the same buffer, after the item array.
        buffer = (ctypes.c_byte * size.value)()
        self._call(
            "PdhGetFormattedCounterArrayW",
            counter,
            fmt,
            ctypes.pointer(size),
            ctypes.pointer(count),
            buffer,
        )
        items = (CounterValueItem * count.value).from_buffer(buffer)
        return {
            item.szName: item.FmtValue.doubleValue
            for item in items
            if item.FmtValue.CStatus in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA)
        }

    def close(self) -> None:
        if self._query is not None:
            self._dll.PdhCloseQuery(self._query)
            self._query = None

    def _call(self, name: str, *args: object) -> None:
        status = getattr(self._dll, name)(*args)
        if status != ERROR_SUCCESS:
            raise PdhError(name, status)
