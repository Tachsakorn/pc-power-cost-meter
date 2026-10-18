import ctypes
import sys
from typing import Final

SPI_GETCLIENTAREAANIMATION: Final = 0x1042


def windows_animations_enabled() -> bool:
    """Windows' "Animation effects" setting; True wherever it can't be read."""
    if sys.platform != "win32":
        return True
    enabled = ctypes.c_int(1)
    try:
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
        )
    except (AttributeError, OSError):
        return True
    return not ok or bool(enabled.value)
