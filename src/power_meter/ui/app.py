import logging
import sys
import tkinter
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from importlib import resources
from pathlib import Path
from types import TracebackType
from typing import Final, Literal

import customtkinter as ctk

from power_meter.power_model import PowerSnapshot
from power_meter.sampler import Sampler
from power_meter.sensors.base import Component
from power_meter.settings import Settings
from power_meter.tariff import HolidayCalendar, tariff_from_settings
from power_meter.ui import theme
from power_meter.ui.main_view import ROW_COUNT, MainView, build_view_model
from power_meter.ui.scene.art import SceneArt
from power_meter.ui.scene.motion import windows_animations_enabled
from power_meter.ui.scene.state import StateTracker, describe
from power_meter.ui.settings_view import SettingsView
from power_meter.ui.stage import WINDOW, Stage
from power_meter.ui.theme import Palette
from power_meter.ui.widgets import CONTROL_H, Button, Painter, label

type Theme = Literal["dark", "light"]

TITLE: Final = "PC Power & Cost Meter"
WIDTH, HEIGHT = WINDOW
POLL_MS: Final = 250
# customtkinter checks the system theme every 30 ms. The app always sets its own theme, so the
# check never changes anything; running it rarely saves waking the CPU 33 times a second.
APPEARANCE_CHECK_MS: Final = 1000
NO_DATA_DIR: Final = "None — no writable folder on this PC"

XS, S, M, L, XL = theme.SPACE

logger = logging.getLogger(__name__)


def palette_for(name: Theme) -> Palette:
    return theme.DARK if name == "dark" else theme.LIGHT


def set_window_icon(window: ctk.CTk) -> None:
    """Replaces customtkinter's default title-bar icon with the app's own."""
    # Tk reads .ico files only on Windows; other platforms keep their default icon.
    if sys.platform != "win32":
        return
    icon = resources.files("power_meter").joinpath("data/icon.ico")
    try:
        with resources.as_file(icon) as path:
            window.iconbitmap(str(path))
    except (OSError, tkinter.TclError) as exc:
        # Only the type: Tk's message includes the icon's full path.
        logger.warning("Could not set the window icon (%s)", type(exc).__name__)


def measured_components(snapshot: PowerSnapshot | None) -> dict[Component, bool]:
    """Which components currently have a measured source. On battery neither part is listed."""
    parts = {part.label: part.measured for part in snapshot.parts} if snapshot else {}
    return {"cpu": parts.get("CPU", False), "gpu": parts.get("GPU", False)}


class App(ctk.CTk):
    def __init__(
        self,
        sampler: Sampler,
        settings: Settings,
        holidays: HolidayCalendar,
        data_dir: Path | None,
        save_settings: Callable[[Settings], None],
        on_close: Callable[[], None],
    ) -> None:
        ctk.AppearanceModeTracker.update_loop_interval = APPEARANCE_CHECK_MS
        ctk.set_appearance_mode(settings.theme)
        super().__init__()
        self._sampler = sampler
        self._settings = settings
        self._holidays = holidays
        self._tariff = tariff_from_settings(settings, holidays)
        self._data_dir = data_dir
        self._save_settings = save_settings
        self._on_close = on_close
        self._save_failed = False
        self._settings_view: SettingsView | None = None
        self._tracker = StateTracker()
        self._rendered: object = None
        self._theme: Theme = settings.theme

        self.title(TITLE)
        set_window_icon(self)
        self.geometry(f"{WIDTH}x{HEIGHT}")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._close)
        # Sent when Windows is shutting down or logging off. The process can end without a
        # close event, so the totals since the last autosave would be lost.
        self.protocol("WM_SAVE_YOURSELF", sampler.save_now)

        palette = palette_for(settings.theme)
        self.configure(fg_color=palette.background)
        self._stage = Stage(
            self, palette, daylight=settings.theme == "light", art=SceneArt.bundled()
        )
        self._stage.pack(fill="both", expand=True)
        self._main_view = MainView(
            self._stage,
            palette,
            on_open_settings=self._open_settings,
            on_reset=sampler.request_reset,
            on_toggle_theme=self._toggle_theme,
            on_hours_change=self._hours_changed,
        )
        self._apply_animation()
        self._poll_job = self.after_idle(self._start)

    def report_callback_exception(
        self, exc: type[BaseException], val: BaseException, tb: TracebackType | None
    ) -> None:
        # The packaged app has no console, so Tk's default stderr report would be lost.
        logger.error("Unhandled error in the window", exc_info=(exc, val, tb))

    def _start(self) -> None:
        # The first readings are shown before the stage starts, so its first backdrop is laid
        # out around the dashboard as it will look.
        try:
            self._poll()
        finally:
            self._stage.start()

    def _poll(self) -> None:
        # Scheduled first, so one failed render doesn't freeze the display for good.
        self._poll_job = self.after(POLL_MS, self._poll)
        can_save = self._data_dir is not None and not self._save_failed
        now = datetime.now()
        rate = self._tariff.rate_at(now)
        state = self._sampler.latest()
        # A new reading comes once a second, so most polls have nothing new to show. The date
        # is in the key because the price line names the day of the next TOU change.
        rendered = (state, self._settings, rate, can_save, now.date())
        if rendered != self._rendered:
            self._main_view.render(build_view_model(state, self._settings, rate, can_save=can_save))
            self._rendered = rendered
        scene = self._tracker.update(state)
        self._stage.show_scene(scene, describe(scene, state.snapshot))

    def _use_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._tariff = tariff_from_settings(settings, self._holidays)
        try:
            self._save_settings(settings)
        except OSError as exc:
            # The message alone: the full path would put the Windows user name in the log.
            logger.warning("Could not save settings: %s", exc.strerror or type(exc).__name__)
            self._save_failed = True
        else:
            self._save_failed = False
        self._sampler.apply_settings(settings)
        self._apply_animation()

    def _apply_animation(self) -> None:
        animate = self._settings.animate_scene and windows_animations_enabled()
        self._stage.set_animate(animate)
        self._main_view.set_animate(animate)

    def _apply_theme(self, name: Theme) -> None:
        if name == self._theme:
            return
        self._theme = name
        ctk.set_appearance_mode(name)
        palette = palette_for(name)
        self.configure(fg_color=palette.background)
        self._main_view.apply_palette(palette)
        if self._settings_view is not None:
            self._settings_view.apply_palette(palette)
        self._stage.set_theme(palette, daylight=name == "light")

    def _toggle_theme(self) -> None:
        name: Theme = "light" if self._settings.theme == "dark" else "dark"
        self._apply_theme(name)
        self._use_settings(replace(self._settings, theme=name))

    def _hours_changed(self, hours: int) -> None:
        self._use_settings(replace(self._settings, hours_per_day=hours))

    def _open_settings(self) -> None:
        if self._settings_view is not None:
            return
        self._settings_view = SettingsView(
            self._stage,
            palette_for(self._settings.theme),
            self._settings,
            str(self._data_dir) if self._data_dir is not None else NO_DATA_DIR,
            measured_components(self._sampler.latest().snapshot),
            on_save=self._settings_saved,
            on_cancel=self._close_settings,
            on_theme_change=self._apply_theme,
        )
        self._main_view.hide()
        self._settings_view.grid(row=0, column=1, rowspan=ROW_COUNT, sticky="nsew")
        # Focus the view itself so the next Tab lands on its first control.
        self._settings_view.focus_set()
        self._stage.request_backdrop()

    def _close_settings(self) -> None:
        if self._settings_view is None:
            return
        self._settings_view.destroy()
        self._settings_view = None
        self._main_view.show()
        self._stage.focus_set()
        self._stage.request_backdrop()

    def _settings_saved(self, settings: Settings) -> None:
        # Leaving the view first means a held Enter can't save the form again.
        self._close_settings()
        self._use_settings(settings)
        self._apply_theme(settings.theme)

    def _close(self) -> None:
        self.after_cancel(self._poll_job)
        # Hidden first: stopping the sampler can wait a few seconds for a sample in progress.
        self.withdraw()
        try:
            self._on_close()
        finally:
            self.destroy()


def show_already_running() -> None:
    ctk.set_appearance_mode("dark")
    window = ctk.CTk()
    window.title(TITLE)
    set_window_icon(window)
    window.resizable(False, False)
    painter = Painter(theme.DARK)
    painter.paint(window, fg_color="background")
    font = ctk.CTkFont(theme.FONT_FAMILY, theme.SIZE_BODY)
    label(painter, window, f"{TITLE} is already running.", font, "text").pack(padx=XL, pady=(XL, M))
    ok = Button(
        window,
        text="OK",
        command=window.destroy,
        font=ctk.CTkFont(theme.FONT_FAMILY, theme.SIZE_BODY, "bold"),
        width=88,
        height=CONTROL_H - XS,
    )
    painter.paint(
        ok,
        fg_color="surface",
        hover_color="background",
        text_color="accent",
        border_color="focus",
    )
    ok.pack(anchor="e", padx=XL, pady=(0, L))
    ok.focus_set()
    window.bind("<Escape>", lambda _e: window.destroy())
    window.mainloop()
