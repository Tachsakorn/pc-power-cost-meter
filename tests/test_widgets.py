import tkinter.font

import pytest

from power_meter.ui import theme
from power_meter.ui.widgets import pick_icons


def installed(monkeypatch: pytest.MonkeyPatch, *families: str) -> None:
    monkeypatch.setattr(tkinter.font, "families", lambda root=None, displayof=None: families)


def test_icons_use_the_first_installed_icon_font(monkeypatch: pytest.MonkeyPatch) -> None:
    installed(monkeypatch, "Segoe UI", "Segoe MDL2 Assets", "Segoe Fluent Icons")

    icons = pick_icons(tkinter.Misc())

    assert icons.family == "Segoe Fluent Icons"
    assert (icons.sun, icons.moon, icons.settings) == ("", "", "")


def test_icons_fall_back_to_older_icon_font(monkeypatch: pytest.MonkeyPatch) -> None:
    installed(monkeypatch, "Segoe UI", "Segoe MDL2 Assets")

    assert pick_icons(tkinter.Misc()).family == "Segoe MDL2 Assets"


def test_icons_fall_back_to_text_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    installed(monkeypatch, "DejaVu Sans")

    icons = pick_icons(tkinter.Misc())

    assert icons.family == theme.FONT_FAMILY
    assert (icons.sun, icons.moon, icons.settings) == ("☀", "☾", "⚙")
