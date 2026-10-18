import time
import tkinter
from collections.abc import Callable, Iterator
from dataclasses import replace
from importlib import resources
from pathlib import Path
from typing import Any, cast

import customtkinter as ctk
import pytest
from PIL import Image

from power_meter import __version__
from power_meter.meter import Totals
from power_meter.power_model import Part, PowerSnapshot
from power_meter.sampler import MeterState, Sampler
from power_meter.settings import MeterType, Settings, default_settings
from power_meter.tariff import HolidayCalendar
from power_meter.ui import app as app_module
from power_meter.ui import stage as stage_module
from power_meter.ui.app import HEIGHT, WIDTH, App, measured_components
from power_meter.ui.main_view import RESET_MIN_GAP_S
from power_meter.ui.scene.backdrop import Backdrop
from power_meter.ui.scene.state import SceneState, caption
from power_meter.ui.stage import Stage
from power_meter.ui.theme import DARK, LIGHT
from power_meter.ui.widgets import HAIR_SPACE, Checkbox


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
        if isinstance(w, ctk.CTkButton)
        and text in (w.cget("text"), getattr(w, "tooltip", ""))
        and w.winfo_ismapped()
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
        assert "1.2847" in texts(app)
        assert "CPU" in texts(app)

        click(app, "Settings")
        assert f"Version {__version__}" in texts(app)
        click(app, "TOU")
        click(app, "Save")
        assert [s.meter_type for s in saved] == [MeterType.TOU]
        assert sampler.applied == saved
        assert any(text.startswith("TOU · ") for text in texts(app))

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


def open_app(
    tmp_path: Path,
    sampler: FakeSampler,
    settings: Settings | None = None,
    on_save: Callable[[Settings], None] = lambda _settings: None,
) -> App:
    try:
        app = App(
            cast(Sampler, sampler),
            settings or default_settings(is_laptop=False),
            HolidayCalendar.bundled(),
            tmp_path,
            on_save,
            lambda: None,
        )
    except tkinter.TclError as exc:
        pytest.skip(f"Tk can't open a window here: {exc}")
    pump(app)
    return app


@pytest.fixture
def backdrops(monkeypatch: pytest.MonkeyPatch) -> list[Backdrop]:
    rendered: list[Backdrop] = []
    original = stage_module.render_backdrop

    def render(*args: Any, **kwargs: Any) -> Backdrop:
        backdrop = original(*args, **kwargs)
        rendered.append(backdrop)
        return backdrop

    monkeypatch.setattr(stage_module, "render_backdrop", render)
    return rendered


def close(app: App) -> None:
    app.tk.call(app.protocol("WM_DELETE_WINDOW"))


def stage_of(app: App) -> Stage:
    [stage] = [w for w in app.winfo_children() if isinstance(w, Stage)]
    return stage


def widget_boxes(stage: Stage) -> list[tuple[int, int, int, int]]:
    return [
        (w.winfo_x(), w.winfo_y(), w.winfo_x() + w.winfo_width(), w.winfo_y() + w.winfo_height())
        for w in stage.winfo_children()
        if w.winfo_ismapped()
    ]


def assert_clear_of_widgets(stage: Stage, backdrop: Backdrop, background: str) -> None:
    plain = Image.new("RGB", (1, 1), background).getpixel((0, 0))
    boxes = widget_boxes(stage)
    assert boxes
    for box in boxes:
        colours = backdrop.image.crop(box).getcolors()
        assert colours is not None
        assert {colour for _, colour in colours} == {plain}, box
    for star in stage.find_withtag("twinkler"):
        left, top, right, bottom = stage.coords(star)
        for x0, y0, x1, y1 in boxes:
            assert right <= x0 or left >= x1 or bottom <= y0 or top >= y1


def caption_text(stage: Stage) -> str:
    return " ".join(stage.itemcget(item, "text") for item in stage.find_withtag("caption"))


def pending_ticks(app: App) -> int:
    jobs = app.tk.splitlist(app.tk.call("after", "info"))
    return sum("_advance" in str(app.tk.call("after", "info", job)[0]) for job in jobs)


def test_window_is_760_by_600(tmp_path: Path) -> None:
    app = open_app(tmp_path, FakeSampler())
    try:
        assert (WIDTH, HEIGHT) == (760, 600)
        assert (app.winfo_width(), app.winfo_height()) == (760, 600)
        assert app.resizable() == (False, False)
        stage = stage_of(app)
        assert (stage.winfo_width(), stage.winfo_height()) == (760, 600)
    finally:
        close(app)


@pytest.mark.parametrize(("name", "palette"), [("dark", DARK), ("light", LIGHT)])
def test_backdrop_keeps_clear_of_widgets(
    tmp_path: Path, backdrops: list[Backdrop], name: str, palette: Any
) -> None:
    settings = replace(default_settings(is_laptop=False), theme=name)
    app = open_app(tmp_path, FakeSampler(), settings)
    try:
        assert_clear_of_widgets(stage_of(app), backdrops[-1], palette.background)
    finally:
        close(app)


def test_backdrop_refreshes_after_settings_and_theme(
    tmp_path: Path, backdrops: list[Backdrop]
) -> None:
    app = open_app(tmp_path, FakeSampler())
    stage = stage_of(app)
    try:
        before = len(backdrops)
        click(app, "Settings")
        assert len(backdrops) > before
        [settings_view] = widget_boxes(stage)
        assert settings_view == (stage.scene_column_px, 0, 760, 600)
        assert_clear_of_widgets(stage, backdrops[-1], DARK.background)

        before = len(backdrops)
        click(app, "Cancel")
        assert len(backdrops) > before
        assert len(widget_boxes(stage)) > 1
        assert_clear_of_widgets(stage, backdrops[-1], DARK.background)

        before = len(backdrops)
        click(app, "Switch to light mode")
        assert len(backdrops) > before
        assert_clear_of_widgets(stage, backdrops[-1], LIGHT.background)
    finally:
        close(app)


def test_scene_caption_follows_sampler_status(tmp_path: Path) -> None:
    sampler = FakeSampler()
    running = sampler.state
    sampler.state = replace(running, snapshot=None, status="warming_up", display_w=None)
    app = open_app(tmp_path, sampler)
    stage = stage_of(app)
    try:
        assert "Waiting for live data" in caption_text(stage)

        sampler.state = running
        pump(app)
        assert caption(SceneState.ACTIVE) in caption_text(stage)
    finally:
        close(app)


def checkbox_of(app: App) -> Checkbox:
    [box] = [w for w in descendants(app) if isinstance(w, Checkbox)]
    return box


def press_space(app: App, box: Checkbox) -> None:
    box.focus_force()
    box.event_generate("<KeyPress-space>")
    box.event_generate("<KeyRelease-space>")
    pump(app, 0.1)


def test_settings_checkbox_turns_animation_off(tmp_path: Path) -> None:
    saved: list[Settings] = []
    app = open_app(tmp_path, FakeSampler(), on_save=saved.append)
    try:
        assert pending_ticks(app) == 1
        click(app, "Settings")
        box = checkbox_of(app)
        assert box.get() is True

        press_space(app, box)
        click(app, "Save")

        assert [s.animate_scene for s in saved] == [False]
        assert pending_ticks(app) == 0
    finally:
        close(app)


def test_cancel_discards_the_animation_choice(tmp_path: Path) -> None:
    saved: list[Settings] = []
    app = open_app(tmp_path, FakeSampler(), on_save=saved.append)
    try:
        click(app, "Settings")
        box = checkbox_of(app)
        press_space(app, box)
        click(app, "Cancel")

        assert saved == []
        assert pending_ticks(app) == 1
        click(app, "Settings")
        assert checkbox_of(app).get() is True
    finally:
        close(app)


def test_turning_animation_back_on_leaves_one_tick_pending(tmp_path: Path) -> None:
    saved: list[Settings] = []
    settings = replace(default_settings(is_laptop=False), animate_scene=False)
    app = open_app(tmp_path, FakeSampler(), settings, on_save=saved.append)
    try:
        assert pending_ticks(app) == 0
        click(app, "Settings")
        press_space(app, checkbox_of(app))
        click(app, "Save")

        assert [s.animate_scene for s in saved] == [True]
        assert pending_ticks(app) == 1
    finally:
        close(app)


def test_tab_from_the_theme_control_reaches_the_checkbox(tmp_path: Path) -> None:
    app = open_app(tmp_path, FakeSampler())
    try:
        click(app, "Settings")
        [light] = [
            w
            for w in descendants(app)
            if isinstance(w, ctk.CTkButton) and w.cget("text") == "Light"
        ]
        light.focus_force()
        pump(app, 0.1)

        assert light.tk_focusNext() is checkbox_of(app)
    finally:
        close(app)


def test_unticked_checkbox_is_outlined_and_hovers_without_filling(tmp_path: Path) -> None:
    app = open_app(tmp_path, FakeSampler())
    try:
        click(app, "Settings")
        box = checkbox_of(app)
        inner = box.winfo_children()[0]
        assert inner.cget("hover_color") == DARK.accent

        press_space(app, box)

        assert inner.cget("border_color") == DARK.text_muted
        assert inner.cget("hover_color") == DARK.surface
        press_space(app, box)
        assert inner.cget("hover_color") == DARK.accent
    finally:
        close(app)


def test_animation_choice_is_kept_when_other_settings_change(tmp_path: Path) -> None:
    saved: list[Settings] = []
    settings = replace(default_settings(is_laptop=False), animate_scene=False)
    app = open_app(tmp_path, FakeSampler(), settings, on_save=saved.append)
    try:
        assert pending_ticks(app) == 0
        click(app, "Settings")
        assert checkbox_of(app).get() is False
        click(app, "TOU")
        click(app, "Save")

        assert saved[-1].animate_scene is False
        assert pending_ticks(app) == 0
    finally:
        close(app)


def test_settings_sections_use_the_dashboard_caption_style(tmp_path: Path) -> None:
    app = open_app(tmp_path, FakeSampler())
    try:
        click(app, "Settings")
        [theme_caption] = [
            w
            for w in descendants(app)
            if isinstance(w, ctk.CTkLabel) and w.cget("text").replace(HAIR_SPACE, "") == "THEME"
        ]

        assert theme_caption.cget("font").cget("size") == 11
        assert theme_caption.cget("text_color") == DARK.text_muted
    finally:
        close(app)


def test_animation_checkbox_follows_the_theme(tmp_path: Path) -> None:
    app = open_app(tmp_path, FakeSampler())
    try:
        click(app, "Settings")
        box = checkbox_of(app)
        assert box.cget("border_color") == DARK.background

        click(app, "Light")

        assert box.cget("border_color") == LIGHT.background
        assert box.winfo_children()[0].cget("fg_color") == LIGHT.accent
    finally:
        close(app)


def test_space_toggles_the_focused_animation_checkbox(tmp_path: Path) -> None:
    app = open_app(tmp_path, FakeSampler())
    try:
        click(app, "Settings")
        box = checkbox_of(app)
        box.focus_force()
        pump(app, 0.1)
        assert app.focus_get() is box

        press_space(app, box)

        assert box.get() is False
    finally:
        close(app)


def test_scene_stays_still_when_windows_animations_are_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "windows_animations_enabled", lambda: False)
    app = open_app(tmp_path, FakeSampler())
    try:
        assert pending_ticks(app) == 0
    finally:
        close(app)
