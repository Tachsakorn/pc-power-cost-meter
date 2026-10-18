import ctypes
import logging
import sys
from typing import Any, Final

MUTEX_NAME: Final = "Local\\PCPowerCostMeter.SingleInstance"
ERROR_ALREADY_EXISTS: Final = 183

logger = logging.getLogger(__name__)

# Windows releases the mutex when the process exits, so the handle is kept until then.
_mutex: int | None = None


def _load_kernel32() -> Any:
    if sys.platform != "win32":
        raise OSError("kernel32 is only available on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.GetLastError.restype = ctypes.c_uint32
    kernel32.GetLastError.argtypes = []
    return kernel32


def acquire(kernel32: object | None = None) -> bool:
    """Claim the app's per-session mutex. False means another copy is already running."""
    global _mutex
    try:
        dll: Any = kernel32 if kernel32 is not None else _load_kernel32()
    except OSError as exc:
        logger.warning("Can't check for another running copy: %s", exc)
        return True
    handle = dll.CreateMutexW(None, False, MUTEX_NAME)
    # With use_last_error, ctypes restores the error saved after CreateMutexW before this call.
    error = dll.GetLastError()
    if not handle:
        logger.warning("CreateMutexW failed with error %d", error)
        return True
    if error == ERROR_ALREADY_EXISTS:
        return False
    _mutex = handle
    return True
