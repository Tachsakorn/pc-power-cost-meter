import math
import time
import tkinter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, NamedTuple

import customtkinter as ctk

from power_meter.projection import MIN_SESSION_S, monthly_cost
from power_meter.sampler import MeterState
from power_meter.settings import FIELD_RANGES, Settings
from power_meter.tariff import RateInfo
from power_meter.ui import theme
from power_meter.ui.format import amount, baht_short, duration, price_line, watts
from power_meter.ui.gauge import (
    STROKE,
    TICK_GAP,
    TICK_MAJOR,
    RingColours,
    on_ring,
    render_ring,
)
from power_meter.ui.stage import Stage
from power_meter.ui.theme import Palette
from power_meter.ui.widgets import (
    Button,
    Painter,
    ghost_button,
    icon_button,
    label,
    pick_icons,
    row,
    set_text,
    spaced,
)

RESET_CONFIRM_MS: Final = 3000
RESET_MIN_GAP_S: Final = 0.4
NO_VALUE: Final = "—"
LIVE: Final = "Live"
STARTING: Final = "Starting up"
RESTARTING: Final = "Measurement paused — restarting"
CANNOT_SAVE: Final = "Settings can't be saved on this PC"
ESTIMATES_ONLY: Final = "Estimates only on this PC"
WAITING: Final = "Waiting for the first reading"
HOURS_MIN, HOURS_MAX = (int(limit) for limit in FIELD_RANGES["hours_per_day"])


@dataclass(frozen=True)
class DetailRow:
    label: str
    watts: str
    detail: str
    measured: bool
    share: float


@dataclass(frozen=True)
class MainViewModel:
    """Everything the main view shows, already formatted."""

    status: str
    live: bool
    watts: str
    load: str
    gauge_fraction: float
    peak: str
    spent: str
    spent_caption: str
    per_hour: str
    monthly: str
    monthly_ready: bool
    hours_per_day: int
    details: tuple[DetailRow, ...]
    price: str
    notice: str | None


def build_view_model(
    state: MeterState, settings: Settings, rate: RateInfo, *, can_save: bool = True
) -> MainViewModel:
    """`rate` is the price in effect now under `settings`, so the price line always matches them."""
    snap, display_w = state.snapshot, state.display_w
    if state.status != "warming_up" and snap is not None and display_w is not None:
        power = f"{display_w:.0f}"
        per_hour = baht_short(display_w * rate.rate / 1000)
        load = snap.usage_label
        fraction = min(max(display_w / snap.peak_w, 0.0), 1.0) if snap.peak_w > 0 else 0.0
        peak = watts(snap.peak_w) if snap.peak_w > 0 else ""
        largest = max((part.watts for part in snap.parts), default=0.0)
        details = tuple(
            DetailRow(
                part.label,
                watts(part.watts),
                part.detail,
                part.measured,
                part.watts / largest if largest > 0 else 0.0,
            )
            for part in snap.parts
        )
    else:
        power, per_hour, load, fraction, peak, details = NO_VALUE, NO_VALUE, "", 0.0, "", ()

    projected = monthly_cost(state.session, settings.hours_per_day)
    if projected is None:
        wait_s = max(1, math.ceil(MIN_SESSION_S - state.session.elapsed_s))
        monthly = f"ready in {wait_s} s"
    else:
        monthly = f"≈ {baht_short(projected)}"

    notice = None
    if state.status == "restarting":
        notice = RESTARTING
    elif not can_save:
        notice = CANNOT_SAVE
    # On battery the total comes from the battery's own discharge reading, so it is measured.
    elif snap is not None and snap.all_estimated and not snap.on_battery:
        notice = ESTIMATES_ONLY

    live = state.status == "running" and snap is not None
    return MainViewModel(
        status=notice or (LIVE if live else STARTING),
        live=live,
        watts=power,
        load=load,
        gauge_fraction=fraction,
        peak=peak,
        spent=amount(state.totals.cost_thb),
        spent_caption=f"{duration(state.totals.elapsed_s)} measured",
        per_hour=per_hour,
        monthly=monthly,
        monthly_ready=projected is not None,
        hours_per_day=settings.hours_per_day,
        details=details,
        price=price_line(settings, rate),
        notice=notice,
    )


XS, S, M, L, XL = theme.SPACE
EDGE: Final = 24
PAD: Final = (20, EDGE)
SIZE_CURRENCY: Final = 22
SIZE_UNIT: Final = 18
STEPPER_BUTTON: Final = 24
BAR_H: Final = 2
READOUT_GAP: Final = 16
SPENT_TOP: Final = 36
END_LABEL_GAP: Final = 2

RING_TAG: Final = "ring"
# Small changes are drawn at once; larger ones ease towards the new reading.
EASE_FROM: Final = 0.015
EASE_STEP: Final = 0.35
EASE_DONE: Final = 0.002
FRAME_MS: Final = 40
# A sixth of a degree of sweep, finer than the ring's anti-aliasing can show.
RING_STEPS: Final = 1560

HEADER_ROW, SPENT_ROW, DETAILS_ROW, FOOTER_ROW = 0, 1, 8, 10
ROW_COUNT: Final = 11


class DetailCells(NamedTuple):
    name: Any
    watts: Any
    dot: Any
    detail: Any
    track: tkinter.Frame
    fill: tkinter.Frame


class MainView:
    """The dashboard: the power ring around the scene, the money figures beside it, and a
    footer across the window."""

    def __init__(
        self,
        stage: Stage,
        palette: Palette,
        on_open_settings: Callable[[], None],
        on_reset: Callable[[], None],
        on_toggle_theme: Callable[[], None],
        on_hours_change: Callable[[int], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stage = stage
        self._k: float = ctk.ScalingTracker.get_widget_scaling(stage)
        self._clock = clock
        self._painter = Painter(palette)
        self._on_reset = on_reset
        self._on_hours_change = on_hours_change
        self._hours = 0
        self._reset_job: str | None = None
        self._reset_armed_at = 0.0
        self._detail_cells: list[DetailCells] = []
        self._parts: list[Any] = []
        self._rules: list[tuple[tkinter.Frame, int, bool]] = []
        self._visible = True
        self._animate = True
        self._target = 0.0
        self._shown: float | None = None
        self._ease_job: str | None = None
        self._ring_key: tuple[int, Palette, tuple[int, int, int]] | None = None
        self._icons = pick_icons(stage)

        family, light = theme.FONT_FAMILY, theme.FONT_LIGHT
        self._font_power = ctk.CTkFont(light, theme.SIZE_POWER)
        self._font_unit = ctk.CTkFont(family, SIZE_UNIT)
        self._font_cost = ctk.CTkFont(light, theme.SIZE_COST)
        self._font_currency = ctk.CTkFont(family, SIZE_CURRENCY)
        self._font_value = ctk.CTkFont(family, theme.SIZE_VALUE)
        self._font_body = ctk.CTkFont(family, theme.SIZE_BODY)
        self._font_caption = ctk.CTkFont(family, theme.SIZE_CAPTION)
        self._font_icon = ctk.CTkFont(self._icons.family, theme.SIZE_BODY + 2)

        stage.grid_columnconfigure(1, weight=1)
        stage.grid_rowconfigure(DETAILS_ROW, weight=1)

        header = self._row_frame(HEADER_ROW, pady=(L, 0))
        header.grid_columnconfigure(1, weight=1)
        self._dot_role = "accent"
        self._status_dot = label(self._painter, header, "●", self._font_caption, self._dot_role)
        self._status_dot.grid(row=0, column=0)
        self._status = label(self._painter, header, "", self._font_body, "text_muted")
        self._status.grid(row=0, column=1, sticky="w", padx=(S, 0))
        self._theme_button = self._hinted_button(header, "", on_toggle_theme)
        self._theme_button.grid(row=0, column=2)
        self._settings_button = self._hinted_button(
            header, self._icons.settings, on_open_settings, "Settings"
        )
        self._settings_button.grid(row=0, column=3, padx=(S, 0))

        self._readout = ctk.CTkFrame(stage, corner_radius=0, border_width=0, width=0, height=0)
        self._painter.paint(self._readout, fg_color="background", bg_color="background")
        self._watts = label(self._painter, self._readout, "", self._font_power, "text")
        self._watts.grid(row=0, column=0, sticky="se")
        label(self._painter, self._readout, "W", self._font_unit, "text_muted").grid(
            row=0,
            column=1,
            sticky="sw",
            padx=(XS, 0),
            pady=(0, self._baseline(self._font_power, self._font_unit)),
        )
        self._load = label(self._painter, self._readout, "", self._font_body, "text_muted")
        self._load.grid(row=1, column=0, columnspan=2)
        self._zero = label(self._painter, stage, "", self._font_caption, "text_muted")
        self._peak = label(self._painter, stage, "", self._font_caption, "text_muted")
        for end in (self._zero, self._peak):
            self._painter.paint(end, bg_color="background")

        self._on_stage(
            self._caption(stage, "Spent since last reset"),
            SPENT_ROW,
            sticky="w",
            pady=(SPENT_TOP, 0),
        )
        cost = self._row_frame(SPENT_ROW + 1, pady=(XS, 0))
        label(self._painter, cost, "฿", self._font_currency, "text_muted").grid(
            row=0,
            column=0,
            sticky="sw",
            pady=(0, self._baseline(self._font_cost, self._font_currency)),
        )
        self._spent = label(self._painter, cost, "", self._font_cost, "accent")
        self._spent.grid(row=0, column=1, sticky="sw", padx=(S, 0))
        self._spent_caption = self._on_stage(
            label(self._painter, stage, "", self._font_body, "text_muted"),
            SPENT_ROW + 2,
            sticky="w",
        )

        self._rule(SPENT_ROW + 3, L)
        figures = self._row_frame(SPENT_ROW + 4, pady=(M, 0))
        figures.grid_columnconfigure(0, weight=1)
        self._per_hour = self._figure(figures, 0, "Per hour, right now")
        self._monthly = self._figure(figures, 1, "Per month")
        self._monthly_role = "text"
        label(self._painter, figures, "Hours used a day", self._font_body, "text_muted").grid(
            row=2, column=0, sticky="w", pady=(S, 0)
        )
        stepper = row(figures)
        stepper.grid(row=2, column=1, sticky="e", pady=(S, 0))
        self._hours_down = self._stepper_button(stepper, "−", -1)
        self._hours_down.grid(row=0, column=0)
        self._hours_value = ctk.CTkLabel(
            stepper, text="", font=self._font_value, width=36, height=0
        )
        self._painter.paint(self._hours_value, text_color="text")
        self._hours_value.grid(row=0, column=1)
        self._hours_up = self._stepper_button(stepper, "+", 1)
        self._hours_up.grid(row=0, column=2)

        self._rule(SPENT_ROW + 5, M)
        self._on_stage(self._caption(stage, "Breakdown"), SPENT_ROW + 6, sticky="w", pady=(M, 0))
        self._details = self._row_frame(DETAILS_ROW, sticky="new", pady=(S, 0))
        self._details.grid_columnconfigure(0, weight=1)
        self._no_details = label(
            self._painter, self._details, WAITING, self._font_body, "text_muted"
        )
        self._no_details.grid(row=0, column=0, columnspan=4, sticky="w")

        self._rule(FOOTER_ROW - 1, 0, wide=True)
        # Ghost buttons pad their text by about S, so the footer is inset by S less on the right
        # to keep Reset's text on the same edge as the buttons above it.
        footer = self._row_frame(FOOTER_ROW, wide=True, padx=(EDGE, EDGE - S), pady=(S, M))
        footer.grid_columnconfigure(2, weight=1)
        self._price = label(self._painter, footer, "", self._font_caption, "text_muted")
        self._price.grid(row=0, column=0, sticky="w")
        ghost_button(
            self._painter, footer, "Change", on_open_settings, self._font_body, "accent"
        ).grid(row=0, column=1, padx=(XS, 0))
        self._reset_button = ghost_button(
            self._painter, footer, "Reset", self._reset_clicked, self._font_body
        )
        self._reset_button.grid(row=0, column=3)

        self._destroy_binding = stage.bind("<Destroy>", lambda _e: self._stop(), add="+")
        ctk.ScalingTracker.add_widget(self._rescale, stage)
        self.apply_palette(palette)
        self._place_ring_labels()

    def render(self, vm: MainViewModel) -> None:
        set_text(self._status, vm.status)
        set_text(self._status_dot, "●" if vm.live else "○")
        dot_role = "accent" if vm.live and vm.notice is None else "text_muted"
        if dot_role != self._dot_role:
            self._dot_role = dot_role
            self._painter.paint(self._status_dot, text_color=dot_role)
        set_text(self._watts, vm.watts)
        set_text(self._load, vm.load)
        set_text(self._zero, "0" if vm.peak else "")
        set_text(self._peak, vm.peak)
        self._show_gauge(vm.gauge_fraction)
        set_text(self._spent, vm.spent)
        set_text(self._spent_caption, vm.spent_caption)
        set_text(self._per_hour, vm.per_hour)
        set_text(self._monthly, vm.monthly)
        monthly_role = "text" if vm.monthly_ready else "text_muted"
        if monthly_role != self._monthly_role:
            self._monthly_role = monthly_role
            self._painter.paint(self._monthly, text_color=monthly_role)
        if vm.hours_per_day != self._hours:
            self._show_hours(vm.hours_per_day)
        self._render_details(vm.details)
        set_text(self._price, vm.price)

    def apply_palette(self, palette: Palette) -> None:
        self._painter.apply_palette(palette)
        dark = palette == theme.DARK
        set_text(self._theme_button, self._icons.sun if dark else self._icons.moon)
        self._theme_button.hint = "Light mode" if dark else "Dark mode"
        self._draw_ring()

    def set_animate(self, on: bool) -> None:
        self._animate = on
        if not on and self._ease_job is not None:
            self._settle_ring()

    def hide(self) -> None:
        self._visible = False
        self._disarm_reset()
        self._stage.hide_hint()
        self._cancel_ease()
        for part in self._parts:
            part.grid_remove()
        for frame in self._placed():
            frame.place_forget()
        self._stage.delete(RING_TAG)
        self._ring_key = None

    def show(self) -> None:
        self._visible = True
        for part in self._parts:
            part.grid()
        self._place_ring_labels()
        self._settle_ring()

    def destroy(self) -> None:
        self._stop()
        self._stage.unbind("<Destroy>", self._destroy_binding)
        self._stage.delete(RING_TAG)
        for part in self._parts:
            part.destroy()
        for frame in self._placed():
            frame.destroy()
        self._parts.clear()

    def _px(self, logical: float) -> int:
        return round(logical * self._k)

    def _baseline(self, large: ctk.CTkFont, small: ctk.CTkFont) -> int:
        """Bottom padding that puts `small` text on the same baseline as `large` beside it."""
        drop: int = large.metrics("descent") - small.metrics("descent")
        return round(drop / self._k)

    def _on_stage(self, widget: Any, grid_row: int, *, wide: bool = False, **grid: Any) -> Any:
        if wide:
            grid.setdefault("padx", (EDGE, EDGE))
            widget.grid(row=grid_row, column=0, columnspan=2, **grid)
        else:
            grid.setdefault("padx", PAD)
            widget.grid(row=grid_row, column=1, **grid)
        if isinstance(widget, ctk.CTkBaseClass):
            self._painter.paint(widget, bg_color="background")
        self._parts.append(widget)
        return widget

    def _row_frame(self, grid_row: int, *, sticky: str = "ew", **grid: Any) -> Any:
        frame = ctk.CTkFrame(self._stage, corner_radius=0, border_width=0)
        self._painter.paint(frame, fg_color="background")
        return self._on_stage(frame, grid_row, sticky=sticky, **grid)

    def _placed(self) -> tuple[Any, ...]:
        """The widgets positioned around the ring rather than gridded."""
        return self._readout, self._zero, self._peak

    def _place_ring_labels(self) -> None:
        """Puts the readout under the dome and the scale's ends below the ring's ends."""
        if not self._visible:
            return
        ring = self._stage.ring
        # CTk scales place() coordinates itself, so these are given unscaled.
        k = self._k
        self._readout.place(
            x=ring.x / k, y=(self._stage.scene_bottom + self._px(READOUT_GAP)) / k, anchor="n"
        )
        # Just past the tips of the ticks at either end, so a long figure runs away from them.
        tip = ring.radius + self._px(STROKE / 2 + TICK_GAP + TICK_MAJOR)
        gap = self._px(END_LABEL_GAP)
        for end, at, anchor in ((self._zero, 0.0, "ne"), (self._peak, 1.0, "nw")):
            x, y = on_ring(tip, at)
            end.place(x=(ring.x + x) / k, y=(ring.y + y + gap) / k, anchor=anchor)

    def _rule(self, grid_row: int, space_above: int, *, wide: bool = False) -> None:
        line = tkinter.Frame(self._stage, height=1, bd=0, highlightthickness=0)
        self._painter.paint(line, bg="line")
        self._on_stage(
            line, grid_row, wide=wide, sticky="ew", **self._rule_padding(space_above, wide)
        )
        self._rules.append((line, space_above, wide))

    def _rule_padding(self, space_above: int, wide: bool) -> dict[str, Any]:
        # Rules and bars are plain Tk frames, so their padding is scaled here rather than by
        # customtkinter.
        pad = (EDGE, EDGE) if wide else PAD
        return {"padx": tuple(self._px(p) for p in pad), "pady": (self._px(space_above), 0)}

    def _caption(self, master: Any, text: str) -> Any:
        return label(self._painter, master, spaced(text.upper()), self._font_caption, "text_muted")

    def _figure(self, master: Any, grid_row: int, caption: str) -> Any:
        pady = (0 if grid_row == 0 else S, 0)
        label(self._painter, master, caption, self._font_body, "text_muted").grid(
            row=grid_row, column=0, sticky="w", pady=pady
        )
        value = label(self._painter, master, "", self._font_value, "text")
        value.grid(row=grid_row, column=1, sticky="e", pady=pady)
        return value

    def _hinted_button(
        self, master: Any, glyph: str, command: Callable[[], None], hint: str = ""
    ) -> Button:
        """An icon button that names its action in a hint while hovered or focused."""

        def click() -> None:
            self._stage.hide_hint()
            command()

        button = icon_button(self._painter, master, glyph, click, self._font_icon, hint)
        for sequence in ("<Enter>", "<FocusIn>"):
            tkinter.Misc.bind(
                button, sequence, lambda _e: self._stage.show_hint(button.hint, button), add="+"
            )
        for sequence in ("<Leave>", "<FocusOut>"):
            tkinter.Misc.bind(button, sequence, lambda _e: self._stage.hide_hint(), add="+")
        return button

    def _stepper_button(self, master: Any, text: str, step: int) -> Button:
        return icon_button(
            self._painter,
            master,
            text,
            lambda: self._step_hours(step),
            self._font_body,
            size=STEPPER_BUTTON,
        )

    def _step_hours(self, step: int) -> None:
        hours = min(max(self._hours + step, HOURS_MIN), HOURS_MAX)
        if hours != self._hours:
            self._show_hours(hours)
            self._on_hours_change(hours)

    def _show_hours(self, hours: int) -> None:
        self._hours = hours
        set_text(self._hours_value, str(hours))
        self._hours_down.configure(state="normal" if hours > HOURS_MIN else "disabled")
        self._hours_up.configure(state="normal" if hours < HOURS_MAX else "disabled")

    def _show_gauge(self, fraction: float) -> None:
        self._target = fraction
        if not self._visible or self._ease_job is not None:
            return
        if self._shown is None or not self._animate or abs(fraction - self._shown) < EASE_FROM:
            self._settle_ring()
        else:
            self._ease()

    def _ease(self) -> None:
        shown = self._target if self._shown is None else self._shown
        shown += (self._target - shown) * EASE_STEP
        if abs(self._target - shown) < EASE_DONE:
            shown = self._target
            self._ease_job = None
        else:
            self._ease_job = self._stage.after(FRAME_MS, self._ease)
        self._shown = shown
        self._draw_ring()

    def _settle_ring(self) -> None:
        self._cancel_ease()
        self._shown = self._target
        self._draw_ring()

    def _cancel_ease(self) -> None:
        if self._ease_job is not None:
            self._stage.after_cancel(self._ease_job)
            self._ease_job = None

    def _draw_ring(self) -> None:
        if not self._visible or self._shown is None:
            return
        ring = self._stage.ring
        palette = self._painter.palette
        step = round(self._shown * RING_STEPS)
        key = (step, palette, ring)
        if key == self._ring_key:
            return
        moved = self._ring_key is None or self._ring_key[2] != ring
        self._ring_key = key
        colours = RingColours(palette.accent, palette.track, palette.track, palette.text_faint)
        image = render_ring(step / RING_STEPS, colours, ring.radius, self._k)
        half = image.width // 2
        self._stage.place_image(RING_TAG, image, ring.x - half, ring.y - half)
        if moved:
            self._stage.request_backdrop()

    def _rescale(self, widget_scaling: float, _window_scaling: float) -> None:
        self._k = widget_scaling
        for line, space_above, wide in self._rules:
            line.grid_configure(**self._rule_padding(space_above, wide))
        for cells in self._detail_cells:
            cells.track.configure(height=self._px(BAR_H))
            cells.track.grid_configure(pady=(self._px(XS), 0))
        # The stage refits its scene in its own scaling callback, which may run after this one.
        self._stage.after_idle(self._refit)

    def _refit(self) -> None:
        self._ring_key = None
        self._place_ring_labels()
        self._draw_ring()

    def _render_details(self, rows: tuple[DetailRow, ...]) -> None:
        if [r.label for r in rows] != [cells.name.cget("text") for cells in self._detail_cells]:
            self._rebuild_detail_rows([r.label for r in rows])
            if rows:
                self._no_details.grid_remove()
            else:
                self._no_details.grid()
            if self._visible:
                self._stage.request_backdrop()
        for cells, detail in zip(self._detail_cells, rows, strict=True):
            set_text(cells.watts, detail.watts)
            set_text(cells.detail, detail.detail)
            dot = "●" if detail.measured else "○"
            if cells.dot.cget("text") != dot:
                cells.dot.configure(text=dot)
                self._painter.paint(
                    cells.dot, text_color="accent" if detail.measured else "text_muted"
                )
            if float(cells.fill.place_info().get("relwidth", -1)) != detail.share:
                cells.fill.place(relx=0, rely=0, relheight=1, relwidth=detail.share)

    def _rebuild_detail_rows(self, names: list[str]) -> None:
        for cells in self._detail_cells:
            for widget in cells:
                self._painter.forget(widget)
            # The fill goes with its track.
            for widget in (cells.name, cells.watts, cells.dot, cells.detail, cells.track):
                widget.destroy()
        self._detail_cells = []
        # Each part takes two grid rows: its figures, then a bar under its name and watts.
        for i, name in enumerate(names):
            text_row, pady = 2 * i, (0 if i == 0 else S, 0)
            name_label = label(self._painter, self._details, name, self._font_body, "text")
            name_label.grid(row=text_row, column=0, sticky="w", pady=pady)
            power = label(self._painter, self._details, "", self._font_body, "text")
            power.grid(row=text_row, column=1, sticky="e", pady=pady)
            dot = label(self._painter, self._details, "", self._font_caption, "text_muted")
            dot.grid(row=text_row, column=2, padx=(M, XS), pady=pady)
            detail = label(self._painter, self._details, "", self._font_caption, "text_muted")
            detail.grid(row=text_row, column=3, sticky="w", pady=pady)
            track = tkinter.Frame(self._details, height=self._px(BAR_H), bd=0, highlightthickness=0)
            self._painter.paint(track, bg="track")
            track.grid(
                row=text_row + 1, column=0, columnspan=2, sticky="ew", pady=(self._px(XS), 0)
            )
            fill = tkinter.Frame(track, bd=0, highlightthickness=0)
            self._painter.paint(fill, bg="text_muted")
            self._detail_cells.append(DetailCells(name_label, power, dot, detail, track, fill))

    def _reset_clicked(self) -> None:
        if self._reset_job is None:
            set_text(self._reset_button, "Click again to reset")
            self._painter.paint(self._reset_button, text_color="error")
            self._reset_armed_at = self._clock()
            self._reset_job = self._stage.after(RESET_CONFIRM_MS, self._end_reset_confirm)
            return
        # A double-click or key repeat is not a deliberate second click.
        if self._clock() - self._reset_armed_at < RESET_MIN_GAP_S:
            return
        self._disarm_reset()
        self._on_reset()

    def _disarm_reset(self) -> None:
        if self._reset_job is not None:
            self._stage.after_cancel(self._reset_job)
            self._end_reset_confirm()

    def _stop(self) -> None:
        """Ends pending work. Also runs when the stage is destroyed, by which time the widgets
        are gone, so Reset's label is left alone."""
        self._visible = False
        ctk.ScalingTracker.remove_widget(self._rescale, self._stage)
        self._cancel_ease()
        if self._reset_job is not None:
            self._stage.after_cancel(self._reset_job)
            self._reset_job = None

    def _end_reset_confirm(self) -> None:
        self._reset_job = None
        set_text(self._reset_button, "Reset")
        self._painter.paint(self._reset_button, text_color="text_muted")
