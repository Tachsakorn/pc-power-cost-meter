import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Final, Literal

import customtkinter as ctk

from power_meter.power_model import PowerSnapshot
from power_meter.sampler import Sampler
from power_meter.sensors.base import Component
from power_meter.settings import Settings
from power_meter.ui import theme
from power_meter.ui.main_view import MainView, build_view_model
from power_meter.ui.settings_view import SettingsView
from power_meter.ui.theme import Palette
from power_meter.ui.widgets import CONTROL_H, Button, Painter, label

type Theme = Literal["dark", "light"]

TITLE: Final = "PC Power & Cost Meter"
# The main view needs about 552 px with details expanded; Settings scrolls inside this.
WIDTH, HEIGHT = 460, 560
POLL_MS: Final = 250
CANNOT_SAVE: Final = "Settings can't be saved on this PC"
NO_DATA_DIR: Final = "None — no writable folder on this PC"

XS, S, M, L, XL = theme.SPACE

logger = logging.getLogger(__name__)


def palette_for(name: Theme) -> Palette:
    return theme.DARK if name == "dark" else theme.LIGHT


def measured_components(snapshot: PowerSnapshot | None) -> dict[Component, bool]:
    """Which components currently have a measured source. On battery neither part is listed."""
    parts = {part.label: part.measured for part in snapshot.parts} if snapshot else {}
    return {"cpu": parts.get("CPU", False), "gpu": parts.get("GPU", False)}


class App(ctk.CTk):
    def __init__(
        self,
        sampler: Sampler,
        settings: Settings,
        data_dir: Path | None,
        save_settings: Callable[[Settings], None],
        on_close: Callable[[], None],
    ) -> None:
        ctk.set_appearance_mode(settings.theme)
        super().__init__()
        self._sampler = sampler
        self._settings = settings
        self._data_dir = data_dir
        self._save_settings = save_settings
        self._on_close = on_close
        self._save_failed = False
        self._settings_view: SettingsView | None = None

        self.title(TITLE)
        self.geometry(f"{WIDTH}x{HEIGHT}")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._close)

        palette = palette_for(settings.theme)
        self.configure(fg_color=palette.background)
        self._main_view = MainView(
            self,
            palette,
            on_open_settings=self._open_settings,
            on_reset=sampler.request_reset,
            on_toggle_theme=self._toggle_theme,
            on_hours_change=self._hours_changed,
        )
        self._main_view.pack(fill="both", expand=True)
        self._poll_job = self.after_idle(self._poll)

    def report_callback_exception(
        self, exc: type[BaseException], val: BaseException, tb: TracebackType | None
    ) -> None:
        # The packaged app has no console, so Tk's default stderr report would be lost.
        logger.error("Unhandled error in the window", exc_info=(exc, val, tb))

    def _poll(self) -> None:
        # Scheduled first, so one failed render doesn't freeze the display for good.
        self._poll_job = self.after(POLL_MS, self._poll)
        vm = build_view_model(self._sampler.latest(), self._settings)
        if vm.notice is None and (self._data_dir is None or self._save_failed):
            vm = replace(vm, notice=CANNOT_SAVE)
        self._main_view.render(vm)

    def _use_settings(self, settings: Settings) -> None:
        self._settings = settings
        try:
            self._save_settings(settings)
        except OSError as exc:
            # The message alone: the full path would put the Windows user name in the log.
            logger.warning("Could not save settings: %s", exc.strerror or type(exc).__name__)
            self._save_failed = True
        else:
            self._save_failed = False
        self._sampler.apply_settings(settings)

    def _apply_theme(self, name: Theme) -> None:
        ctk.set_appearance_mode(name)
        palette = palette_for(name)
        self.configure(fg_color=palette.background)
        self._main_view.apply_palette(palette)
        if self._settings_view is not None:
            self._settings_view.apply_palette(palette)

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
            self,
            palette_for(self._settings.theme),
            self._settings,
            str(self._data_dir) if self._data_dir is not None else NO_DATA_DIR,
            measured_components(self._sampler.latest().snapshot),
            on_save=self._settings_saved,
            on_cancel=self._close_settings,
            on_theme_change=self._apply_theme,
        )
        self._main_view.pack_forget()
        self._settings_view.pack(fill="both", expand=True)
        # Focus the view itself so the next Tab lands on its first control.
        self._settings_view.focus_set()

    def _close_settings(self) -> None:
        if self._settings_view is None:
            return
        self._settings_view.destroy()
        self._settings_view = None
        self._main_view.pack(fill="both", expand=True)
        self._main_view.focus_set()

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
    window.resizable(False, False)
    painter = Painter(theme.DARK)
    painter.paint(window, fg_color="background")
    font = ctk.CTkFont(theme.FONT_FAMILY, theme.SIZE_BODY)
    label(painter, window, f"{TITLE} is already running.", font, "text").pack(
        padx=XL, pady=(XL, M)
    )
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
        text_color="accent_power",
        border_color="focus",
    )
    ok.pack(anchor="e", padx=XL, pady=(0, L))
    ok.focus_set()
    window.bind("<Escape>", lambda _e: window.destroy())
    window.mainloop()
