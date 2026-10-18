from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Palette:
    background: str
    surface: str
    text: str
    text_muted: str
    accent_cost: str
    accent_power: str
    error: str
    focus: str


DARK: Final = Palette(
    background="#1A1B26",
    surface="#22243A",
    text="#E6E7EE",
    text_muted="#9A9CB0",
    accent_cost="#2ECC71",
    accent_power="#3498DB",
    error="#FF6B6B",
    focus="#3498DB",
)

LIGHT: Final = Palette(
    background="#F5F6FA",
    surface="#FFFFFF",
    text="#1A1B26",
    text_muted="#5A5D72",
    accent_cost="#1E8449",
    accent_power="#1F6FA8",
    error="#C0392B",
    focus="#1F6FA8",
)

FONT_FAMILY: Final = "Segoe UI"
SIZE_COST: Final = 40
SIZE_POWER: Final = 22
SIZE_BODY: Final = 13
SIZE_CAPTION: Final = 11

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
