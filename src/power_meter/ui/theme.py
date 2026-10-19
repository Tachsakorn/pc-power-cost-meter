from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Palette:
    background: str
    surface: str
    line: str
    text: str
    text_muted: str
    text_faint: str
    accent: str
    track: str
    error: str
    focus: str


DARK: Final = Palette(
    background="#1A1613",
    surface="#231E1A",
    line="#332B25",
    text="#F0E8DC",
    text_muted="#A3968A",
    text_faint="#6E635A",
    accent="#E8B866",
    track="#3A312A",
    error="#FF8A70",
    focus="#E8B866",
)

LIGHT: Final = Palette(
    background="#F4EEE4",
    surface="#FBF7F0",
    line="#E2D8C8",
    text="#2A2119",
    text_muted="#74675A",
    text_faint="#A99C8E",
    accent="#9A5F0E",
    track="#E6DDCF",
    error="#B3261E",
    focus="#9A5F0E",
)

FONT_FAMILY: Final = "Segoe UI"
FONT_LIGHT: Final = "Segoe UI Light"
ICON_FONTS: Final = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
SIZE_COST: Final = 44
SIZE_POWER: Final = 46
SIZE_BODY: Final = 13
SIZE_CAPTION: Final = 11
SIZE_VALUE: Final = 17

SPACE: Final = (4, 8, 12, 16, 24)
RADIUS: Final = 8


def _luminance(colour: str) -> float:
    channels = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio between two `#RRGGBB` colours."""
    lighter, darker = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)
