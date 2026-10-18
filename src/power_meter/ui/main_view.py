import time
import tkinter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

import customtkinter as ctk

from power_meter.projection import monthly_cost
from power_meter.sampler import MeterState
from power_meter.settings import FIELD_RANGES, Settings
from power_meter.ui import theme
from power_meter.ui.format import baht, baht_short, duration, price_line, watts
from power_meter.ui.theme import Palette

RESET_CONFIRM_MS: Final = 3000
RESET_MIN_GAP_S: Final = 0.4
NO_VALUE: Final = "—"
HOURS_MIN, HOURS_MAX = (int(limit) for limit in FIELD_RANGES["hours_per_day"])


@dataclass(frozen=True)
class MainViewModel:
    """Everything the main view shows, already formatted."""

    spent: str
    spent_caption: str
    power: str
    per_hour: str
    usage_label: str
    usage_fraction: float
    monthly: str
    hours_per_day: int
    details: tuple[tuple[str, str, str], ...]
    price: str
    notice: str | None


def build_view_model(state: MeterState, settings: Settings) -> MainViewModel:
    snap, display_w = state.snapshot, state.display_w
    if state.status != "warming_up" and snap is not None and display_w is not None:
        power = watts(display_w)
        per_hour = f"about {baht_short(display_w * state.rate.rate / 1000)} per hour"
        usage_label = snap.usage_label
        usage_fraction = min(max(snap.wall_w / snap.peak_w, 0.0), 1.0) if snap.peak_w > 0 else 0.0
        details = tuple((p.label, watts(p.watts), p.detail) for p in snap.parts)
    else:
        power, per_hour, usage_label, usage_fraction, details = NO_VALUE, "", "", 0.0, ()

    # Projected from the current hours setting rather than state.monthly_thb, so the
    # figure follows the hours spinner immediately instead of on the sampler's next tick.
    projected = monthly_cost(state.session, settings.hours_per_day)

    notice = None
    if state.status == "restarting":
        notice = "Measurement paused — restarting"
    # On battery the total comes from the battery's own discharge reading, so it is measured.
    elif snap is not None and snap.all_estimated and not snap.on_battery:
        notice = "Estimates only on this PC"

    return MainViewModel(
        spent=baht(state.totals.cost_thb),
        spent_caption=f"in {duration(state.totals.elapsed_s)}",
        power=power,
        per_hour=per_hour,
        usage_label=usage_label,
        usage_fraction=usage_fraction,
        monthly="Measuring…" if projected is None else f"≈ {baht_short(projected)} per month",
        hours_per_day=settings.hours_per_day,
        details=details,
        price=f"Price: {price_line(settings, state.rate)}",
        notice=notice,
    )


XS, S, M, L, XL = theme.SPACE
FOCUS_RING_W: Final = 2


class _Button(ctk.CTkButton):
    """A CTkButton that can be reached with Tab, shows a focus ring and responds to Enter/Space."""

    def __init__(self, master: Any, **kwargs: Any) -> None:
        super().__init__(master, border_width=0, corner_radius=theme.RADIUS, **kwargs)
        # CTkButton forwards only `cursor` to its frame. Making the frame itself focusable
        # gives one Tab stop per button instead of stops on its inner canvas and label.
        tkinter.Frame.configure(self, takefocus=1)
        tkinter.Misc.bind(self, "<FocusIn>", lambda _e: self.configure(border_width=FOCUS_RING_W))
        tkinter.Misc.bind(self, "<FocusOut>", lambda _e: self.configure(border_width=0))
        # On release, not press: a held key auto-repeats KeyPress and would fire repeatedly.
        tkinter.Misc.bind(self, "<KeyRelease-Return>", lambda _e: self.invoke())
        tkinter.Misc.bind(self, "<KeyRelease-space>", lambda _e: self.invoke())

    # CTkButton sends focus to its inner label, which has none of the key bindings above.
    def focus_set(self) -> None:
        tkinter.Misc.focus_set(self)

    def focus_force(self) -> None:
        tkinter.Misc.focus_force(self)

    focus = focus_set


def _row(master: Any) -> Any:
    return ctk.CTkFrame(master, corner_radius=0, fg_color="transparent")


def _set_text(widget: Any, text: str) -> None:
    if widget.cget("text") != text:
        widget.configure(text=text)


class MainView(ctk.CTkFrame):
    def __init__(
        self,
        master: Any,
        palette: Palette,
        on_open_settings: Callable[[], None],
        on_reset: Callable[[], None],
        on_toggle_theme: Callable[[], None],
        on_hours_change: Callable[[int], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(master, corner_radius=0)
        self._clock = clock
        self._palette = palette
        self._on_reset = on_reset
        self._on_hours_change = on_hours_change
        self._roles: dict[Any, dict[str, str]] = {}
        self._hours = 0
        self._reset_job: str | None = None
        self._reset_armed_at = 0.0
        self._detail_cells: list[tuple[Any, Any, Any]] = []

        self._font_cost = ctk.CTkFont(theme.FONT_FAMILY, theme.SIZE_COST, "bold")
        self._font_power = ctk.CTkFont(theme.FONT_FAMILY, theme.SIZE_POWER, "bold")
        self._font_body = ctk.CTkFont(theme.FONT_FAMILY, theme.SIZE_BODY)
        self._font_strong = ctk.CTkFont(theme.FONT_FAMILY, theme.SIZE_BODY, "bold")
        self._font_caption = ctk.CTkFont(theme.FONT_FAMILY, theme.SIZE_CAPTION)

        self._paint(self, fg_color="background")
        self.grid_columnconfigure(0, weight=1)

        # Ghost buttons pad their text by about S, so rows that end in one are inset by S
        # less on that side to keep the text on the same edge as everything else.
        header = _row(self)
        header.grid(row=0, column=0, sticky="ew", padx=(XL, XL - S), pady=(L, 0))
        header.grid_columnconfigure(0, weight=1)
        self._label(header, "Power & Cost Meter", self._font_strong, "text").grid(
            row=0, column=0, sticky="w"
        )
        self._theme_button = self._ghost_button(header, "", on_toggle_theme)
        self._theme_button.grid(row=0, column=1)
        self._ghost_button(header, "Settings", on_open_settings).grid(row=0, column=2)

        self._caption(self, "You've spent").grid(row=1, column=0, sticky="w", padx=XL, pady=(L, 0))
        self._spent = self._label(self, "", self._font_cost, "accent_cost")
        self._spent.grid(row=2, column=0, sticky="w", padx=XL)
        self._spent_caption = self._caption(self, "")
        self._spent_caption.grid(row=3, column=0, sticky="w", padx=XL)

        now_head = _row(self)
        now_head.grid(row=4, column=0, sticky="ew", padx=XL, pady=(L, 0))
        now_head.grid_columnconfigure(0, weight=1)
        self._caption(now_head, "Right now").grid(row=0, column=0, sticky="w")
        self._notice = self._label(now_head, "", self._font_caption, "text_muted")
        self._notice.grid(row=0, column=1, sticky="e")
        self._notice.grid_remove()

        now_row = _row(self)
        now_row.grid(row=5, column=0, sticky="ew", padx=XL)
        self._power = self._label(now_row, "", self._font_power, "accent_power")
        self._power.grid(row=0, column=0, sticky="sw")
        self._per_hour = self._label(now_row, "", self._font_body, "text")
        baseline = self._font_power.metrics("descent") - self._font_body.metrics("descent")
        self._per_hour.grid(row=0, column=1, sticky="sw", padx=(M, 0), pady=(0, baseline))

        usage_row = _row(self)
        usage_row.grid(row=6, column=0, sticky="ew", padx=XL, pady=(XS, 0))
        self._usage_bar = ctk.CTkProgressBar(
            usage_row, width=160, height=S, corner_radius=theme.RADIUS
        )
        self._paint(self._usage_bar, fg_color="surface", progress_color="accent_power")
        self._usage_bar.set(0)
        self._usage_bar.grid(row=0, column=0)
        self._usage = self._label(usage_row, "", self._font_body, "text_muted")
        self._usage.grid(row=0, column=1, sticky="w", padx=(M, 0))

        hours_row = _row(self)
        hours_row.grid(row=7, column=0, sticky="ew", padx=XL, pady=(L, 0))
        self._caption(hours_row, "If you use your PC like this").grid(row=0, column=0)
        stepper = ctk.CTkFrame(hours_row, corner_radius=theme.RADIUS)
        self._paint(stepper, fg_color="surface")
        stepper.grid(row=0, column=1, padx=S)
        self._hours_down = self._stepper_button(stepper, "−", -1)
        self._hours_down.grid(row=0, column=0)
        self._hours_value = ctk.CTkLabel(
            stepper, text="", font=self._font_strong, width=24, height=0
        )
        self._paint(self._hours_value, text_color="text")
        self._hours_value.grid(row=0, column=1)
        self._hours_up = self._stepper_button(stepper, "+", 1)
        self._hours_up.grid(row=0, column=2)
        self._caption(hours_row, "hours a day").grid(row=0, column=2)
        self._monthly = self._label(self, "", self._font_strong, "text")
        self._monthly.grid(row=8, column=0, sticky="w", padx=XL, pady=(XS, 0))

        self._details_toggle = self._ghost_button(self, "▸ Show details", self._toggle_details)
        self._details_toggle.grid(row=9, column=0, sticky="w", padx=XL - S, pady=(M, 0))
        self._details = ctk.CTkFrame(self, corner_radius=theme.RADIUS)
        self._paint(self._details, fg_color="surface")
        self._details.grid_columnconfigure(2, weight=1)
        self._details.grid(row=10, column=0, sticky="ew", padx=XL, pady=(XS, 0))
        self._details.grid_remove()

        price_row = _row(self)
        price_row.grid(row=11, column=0, sticky="ew", padx=XL, pady=(M, 0))
        self._price = self._label(price_row, "", self._font_caption, "text_muted")
        self._price.grid(row=0, column=0, sticky="w")
        change = self._ghost_button(price_row, "Change", on_open_settings, "accent_power")
        change.grid(row=0, column=1, padx=(XS, 0))
        self._reset_button = self._ghost_button(self, "Reset", self._reset_clicked)
        self._reset_button.grid(row=12, column=0, sticky="e", padx=XL - S, pady=(0, L))

        self.apply_palette(palette)

    def render(self, vm: MainViewModel) -> None:
        _set_text(self._spent, vm.spent)
        _set_text(self._spent_caption, vm.spent_caption)
        _set_text(self._power, vm.power)
        _set_text(self._per_hour, vm.per_hour)
        _set_text(self._usage, vm.usage_label)
        if self._usage_bar.get() != vm.usage_fraction:
            self._usage_bar.set(vm.usage_fraction)
        _set_text(self._monthly, vm.monthly)
        if vm.hours_per_day != self._hours:
            self._show_hours(vm.hours_per_day)
        self._render_details(vm.details)
        _set_text(self._price, vm.price)
        if vm.notice is None:
            self._notice.grid_remove()
        else:
            _set_text(self._notice, vm.notice)
            if not self._notice.winfo_manager():
                self._notice.grid()

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        for widget in self._roles:
            self._apply_roles(widget)
        _set_text(self._theme_button, "Light mode" if palette == theme.DARK else "Dark mode")

    def destroy(self) -> None:
        if self._reset_job is not None:
            self.after_cancel(self._reset_job)
            self._reset_job = None
        super().destroy()

    def _paint(self, widget: Any, **roles: str) -> None:
        """Colours `widget` from palette roles (e.g. text_color="text_muted") and remembers
        them for apply_palette."""
        self._roles.setdefault(widget, {}).update(roles)
        self._apply_roles(widget)

    def _apply_roles(self, widget: Any) -> None:
        roles = self._roles[widget]
        widget.configure(**{option: getattr(self._palette, role) for option, role in roles.items()})

    def _label(self, master: Any, text: str, font: Any, role: str) -> Any:
        label = ctk.CTkLabel(master, text=text, font=font, anchor="w", justify="left", height=0)
        self._paint(label, text_color=role)
        return label

    def _caption(self, master: Any, text: str) -> Any:
        return self._label(master, text, self._font_body, "text_muted")

    def _ghost_button(
        self,
        master: Any,
        text: str,
        command: Callable[[], None],
        text_role: str = "text_muted",
    ) -> _Button:
        button = _Button(
            master, text=text, command=command, font=self._font_body, width=0, height=28
        )
        self._paint(
            button,
            fg_color="background",
            hover_color="surface",
            text_color=text_role,
            border_color="focus",
        )
        return button

    def _stepper_button(self, master: Any, text: str, step: int) -> _Button:
        button = _Button(
            master,
            text=text,
            command=lambda: self._step_hours(step),
            font=self._font_strong,
            width=28,
            height=28,
        )
        self._paint(
            button,
            fg_color="surface",
            hover_color="background",
            text_color="text",
            text_color_disabled="text_muted",
            border_color="focus",
        )
        return button

    def _step_hours(self, step: int) -> None:
        hours = min(max(self._hours + step, HOURS_MIN), HOURS_MAX)
        if hours != self._hours:
            self._show_hours(hours)
            self._on_hours_change(hours)

    def _show_hours(self, hours: int) -> None:
        self._hours = hours
        _set_text(self._hours_value, str(hours))
        self._hours_down.configure(state="normal" if hours > HOURS_MIN else "disabled")
        self._hours_up.configure(state="normal" if hours < HOURS_MAX else "disabled")

    def _toggle_details(self) -> None:
        if self._details.winfo_manager():
            self._details.grid_remove()
            _set_text(self._details_toggle, "▸ Show details")
        else:
            self._details.grid()
            _set_text(self._details_toggle, "▾ Hide details")

    def _render_details(self, rows: tuple[tuple[str, str, str], ...]) -> None:
        if [row[0] for row in rows] != [cells[0].cget("text") for cells in self._detail_cells]:
            self._rebuild_detail_rows(len(rows))
        for cells, row in zip(self._detail_cells, rows, strict=True):
            for cell, text in zip(cells, row, strict=True):
                _set_text(cell, text)

    def _rebuild_detail_rows(self, count: int) -> None:
        for cells in self._detail_cells:
            for cell in cells:
                del self._roles[cell]
                cell.destroy()
        self._detail_cells = []
        last = count - 1
        for i in range(count):
            pady = (M if i == 0 else XS, M if i == last else 0)
            name = self._label(self._details, "", self._font_body, "text")
            name.grid(row=i, column=0, sticky="w", padx=(M, L), pady=pady)
            power = self._label(self._details, "", self._font_body, "text")
            power.grid(row=i, column=1, sticky="e", pady=pady)
            detail = self._label(self._details, "", self._font_caption, "text_muted")
            detail.grid(row=i, column=2, sticky="w", padx=(L, M), pady=pady)
            self._detail_cells.append((name, power, detail))

    def _reset_clicked(self) -> None:
        if self._reset_job is None:
            _set_text(self._reset_button, "Click again to reset")
            self._paint(self._reset_button, text_color="error")
            self._reset_armed_at = self._clock()
            self._reset_job = self.after(RESET_CONFIRM_MS, self._end_reset_confirm)
            return
        # A double-click or key repeat is not a deliberate second click.
        if self._clock() - self._reset_armed_at < RESET_MIN_GAP_S:
            return
        self.after_cancel(self._reset_job)
        self._end_reset_confirm()
        self._on_reset()

    def _end_reset_confirm(self) -> None:
        self._reset_job = None
        _set_text(self._reset_button, "Reset")
        self._paint(self._reset_button, text_color="text_muted")
