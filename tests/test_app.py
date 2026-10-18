import time
import tkinter
from collections.abc import Iterator
from importlib import resources
from pathlib import Path
from typing import Any, cast

import customtkinter as ctk
import pytest

from power_meter.meter import Totals
from power_meter.power_model import Part, PowerSnapshot
from power_meter.sampler import MeterState, Sampler
from power_meter.settings import MeterType, Settings, default_settings
from power_meter.tariff import HolidayCalendar
from power_meter.ui.app import App, measured_components
from power_meter.ui.main_view import RESET_MIN_GAP_S


def snapshot(*parts: Part, on_battery: bool = False) -> PowerSnapshot:
    return PowerSnapshot(
        wall_w=100.0,
        parts=parts,
        on_battery=on_battery,
        usage_label="Light use",
        all_estimated=False,
        peak_w=300.0,
    )


def test_measured_components_follow_the_cpu_and_gpu_parts() -> None:
    snap = snapshot(
        Part("CPU", 40.0, True, "measured"),
        Part("GPU", 20.0, False, "estimated"),
        Part("Other", 25.0, False, "estimated"),
    )

    assert measured_components(snap) == {"cpu": True, "gpu": False}


def test_nothing_counts_as_measured_while_warming_up() -> None:
    assert measured_components(None) == {"cpu": False, "gpu": False}


def test_nothing_counts_as_measured_on_battery() -> None:
    snap = snapshot(
        Part("Battery", 30.0, True, "measured"),
        Part("Charging loss", 3.0, False, "90% efficiency"),
        on_battery=True,
    )

    assert measured_components(snap) == {"cpu": False, "gpu": False}


def test_window_icon_ships_with_the_package() -> None:
    icon = resources.files("power_meter").joinpath("data/icon.ico").read_bytes()

    assert icon[:4] == b"\x00\x00\x01\x00"


class FakeSampler:
    def __init__(self) -> None:
        self.state = MeterState(
            snapshot=snapshot(Part("CPU", 40.0, True, "measured")),
            totals=Totals(1.2847, 0.2855, 8100.0),
            session=Totals(0.05, 0.011, 600.0),
            status="running",
            display_w=100.0,
        )
        self.applied: list[Settings] = []
        self.resets = 0
        self.saves = 0

    def latest(self) -> MeterState:
        return self.state

    def apply_settings(self, settings: Settings) -> None:
        self.applied.append(settings)

    def request_reset(self) -> None:
        self.resets += 1

    def save_now(self, timeout: float = 2.0) -> None:
        self.saves += 1


def descendants(widget: Any) -> Iterator[Any]:
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def texts(window: ctk.CTk) -> list[str]:
    return [
        w.cget("text")
        for w in descendants(window)
        if isinstance(w, ctk.CTkLabel | ctk.CTkButton) and w.winfo_ismapped()
    ]


def pump(window: ctk.CTk, seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        window.update()
        time.sleep(0.01)


def click(window: ctk.CTk, text: str) -> None:
    [button] = [
        w
        for w in descendants(window)
        if isinstance(w, ctk.CTkButton) and w.cget("text") == text and w.winfo_ismapped()
    ]
    button.invoke()
    pump(window)


def test_window_smoke(tmp_path: Path) -> None:
    sampler = FakeSampler()
    saved: list[Settings] = []
    closes: list[None] = []
    try:
        app = App(
            cast(Sampler, sampler),
            default_settings(is_laptop=False),
            HolidayCalendar.bundled(),
            tmp_path,
            saved.append,
            lambda: closes.append(None),
        )
    except tkinter.TclError as exc:
        pytest.skip(f"Tk can't open a window here: {exc}")
    try:
        pump(app)
        assert "฿ 1.2847" in texts(app)

        click(app, "▸ Show details")
        assert "▾ Hide details" in texts(app)

        click(app, "Settings")
        click(app, "TOU")
        click(app, "Save")
        assert [s.meter_type for s in saved] == [MeterType.TOU]
        assert sampler.applied == saved
        assert any(text.startswith("Price: TOU") for text in texts(app))

        click(app, "Reset")
        assert sampler.resets == 0
        time.sleep(RESET_MIN_GAP_S)
        click(app, "Click again to reset")
        assert sampler.resets == 1

        app.tk.call(app.protocol("WM_SAVE_YOURSELF"))
        assert sampler.saves == 1

        app.tk.call(app.protocol("WM_DELETE_WINDOW"))
        assert len(closes) == 1
    finally:
        if not closes:
            app.destroy()
