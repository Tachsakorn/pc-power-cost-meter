"""Tells discrete GPUs apart from integrated and software adapters using DXCore.

DXCore objects are COM interfaces, called here through their vtables. Method slots follow the
declaration order in dxcore_interface.h, after the three IUnknown slots.
"""

import ctypes
import logging
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_RELEASE = 2
_FACTORY_CREATE_ADAPTER_LIST = 3
_LIST_GET_ADAPTER = 3
_LIST_GET_ADAPTER_COUNT = 4
_ADAPTER_GET_PROPERTY = 6

_PROPERTY_INSTANCE_LUID = 0
_PROPERTY_IS_HARDWARE = 11
_PROPERTY_IS_INTEGRATED = 12


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


def _guid(text: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


_IID_FACTORY = _guid("78ee5945-c36e-4b13-a669-005dd11c0f06")
_IID_ADAPTER_LIST = _guid("526c7776-40e9-459b-b711-f32ad76dfc28")
_IID_ADAPTER = _guid("f0db4c7f-fe5a-42a2-bd62-f2a6cf6fc83e")
_ATTRIBUTE_D3D12_GRAPHICS = _guid("0c9ece4d-2f6e-4f01-8c96-e89e331b47b1")


@dataclass(frozen=True)
class AdapterInfo:
    luid: str
    is_hardware: bool
    is_integrated: bool


def luid_key(high: int, low: int) -> str:
    """Adapter LUID in the form PDH uses in GPU Engine instance names."""
    return f"0x{high & 0xFFFFFFFF:08x}_0x{low & 0xFFFFFFFF:08x}"


def discrete_luids(adapters: Iterable[AdapterInfo]) -> frozenset[str]:
    return frozenset(a.luid for a in adapters if a.is_hardware and not a.is_integrated)


def discrete_gpu_luids(dll: object | None = None) -> frozenset[str] | None:
    """LUID keys of discrete hardware GPUs, or None when DXCore can't tell."""
    try:
        adapters = _list_adapters(dll if dll is not None else _load_dxcore())
    except Exception as exc:  # a bad COM pointer raises ValueError; startup must go on regardless
        logger.warning("Can't classify GPU adapters with DXCore: %s", exc)
        return None
    return discrete_luids(adapters)


def _load_dxcore() -> Any:
    if sys.platform != "win32":
        raise OSError("DXCore is only available on Windows")
    return ctypes.WinDLL("dxcore")


def _check(name: str, hresult: int) -> None:
    if hresult & 0x80000000:
        raise OSError(f"{name} failed with HRESULT 0x{hresult & 0xFFFFFFFF:08X}")


def _method(obj: ctypes.c_void_p, slot: int, restype: Any, *argtypes: Any) -> Any:
    if sys.platform != "win32":
        raise OSError("COM calls are only available on Windows")
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return prototype(vtable[slot])


def _release(obj: ctypes.c_void_p) -> None:
    _method(obj, _RELEASE, ctypes.c_ulong)(obj)


def _list_adapters(dll: Any) -> list[AdapterInfo]:
    factory = ctypes.c_void_p()
    _check(
        "DXCoreCreateAdapterFactory",
        dll.DXCoreCreateAdapterFactory(ctypes.byref(_IID_FACTORY), ctypes.byref(factory)),
    )
    try:
        adapter_list = ctypes.c_void_p()
        create_list = _method(
            factory,
            _FACTORY_CREATE_ADAPTER_LIST,
            ctypes.c_long,
            ctypes.c_uint32,
            ctypes.POINTER(_GUID),
            ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        attributes = (_GUID * 1)(_ATTRIBUTE_D3D12_GRAPHICS)
        _check(
            "CreateAdapterList",
            create_list(
                factory, 1, attributes, ctypes.byref(_IID_ADAPTER_LIST), ctypes.byref(adapter_list)
            ),
        )
        try:
            count = _method(adapter_list, _LIST_GET_ADAPTER_COUNT, ctypes.c_uint32)(adapter_list)
            return [_adapter_info(adapter_list, index) for index in range(count)]
        finally:
            _release(adapter_list)
    finally:
        _release(factory)


def _adapter_info(adapter_list: ctypes.c_void_p, index: int) -> AdapterInfo:
    adapter = ctypes.c_void_p()
    get_adapter = _method(
        adapter_list,
        _LIST_GET_ADAPTER,
        ctypes.c_long,
        ctypes.c_uint32,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    )
    _check(
        "GetAdapter",
        get_adapter(adapter_list, index, ctypes.byref(_IID_ADAPTER), ctypes.byref(adapter)),
    )
    try:
        get_property = _method(
            adapter,
            _ADAPTER_GET_PROPERTY,
            ctypes.c_long,
            ctypes.c_uint32,
            ctypes.c_size_t,
            ctypes.c_void_p,
        )
        luid = _LUID()
        is_hardware = ctypes.c_bool()
        is_integrated = ctypes.c_bool()
        for prop, value in (
            (_PROPERTY_INSTANCE_LUID, luid),
            (_PROPERTY_IS_HARDWARE, is_hardware),
            (_PROPERTY_IS_INTEGRATED, is_integrated),
        ):
            _check(
                "GetProperty",
                get_property(adapter, prop, ctypes.sizeof(value), ctypes.byref(value)),
            )
        return AdapterInfo(
            luid_key(luid.HighPart, luid.LowPart), is_hardware.value, is_integrated.value
        )
    finally:
        _release(adapter)
