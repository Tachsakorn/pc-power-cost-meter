import time
import tkinter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, NamedTuple

import customtkinter as ctk

from power_meter.projection import monthly_cost
from power_meter.sampler import MeterState
from power_meter.settings import FIELD_RANGES, Settings
from power_meter.tariff import RateInfo
from power_meter.ui import theme
from power_meter.ui.format import amount, baht_short, duration, price_line, watts
from power_meter.ui.gauge import TICKS, render_gauge
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
MEASURING: Final = "Measuring"
RESTARTING: Final = "Measurement paused — restarting"
CANNOT_SAVE: Final = "Settings can't be saved on this PC"
ESTIMATES_ONLY: Final = "Estimates only on this PC"
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

    notice = None
    if state.status == "restarting":
        notice = RESTARTING
    elif not can_save:
        notice = CANNOT_SAVE
    # On battery the total comes from the battery's own discharge reading, so it is measured.
    elif snap is not None and snap.all_estimated and not snap.on_battery:
        notice = ESTIMATES_ONLY

    return MainViewModel(
        status=notice or MEASURING,
        live=state.status == "running" and snap is not None,
        watts=power,
        load=load,
        gauge_fraction=fraction,
        peak=peak,
        spent=amount(state.totals.cost_thb),
        spent_caption=f"in {duration(state.totals.elapsed_s)}",
        per_hour=per_hour,
        monthly="Measuring…" if projected is None else f"≈ {baht_short(projected)}",
        hours_per_day=settings.hours_per_day,
        details=details,
        price=price_line(settings, rate),
        notice=notice,
    )


XS, S, M, L, XL = theme.SPACE
PAD: Final = (20, 24)
GAUGE_TAG: Final = "gauge"
GAUGE_W, GAUGE_H = 320, 160
GAUGE_ROW_H: Final = 178
# Where the readout and the scale labels sit inside the gauge row, matched to the arc that
# render_gauge draws: centre 128 px down, ends 116 px either side of it.
WATTS_TOP: Final = 46
LOAD_TOP: Final = 116
ARC_ENDS_X: Final = (44, 268)
SIZE_CURRENCY: Final = 22
SIZE_UNIT: Final = 18
STEPPER_BUTTON: Final = 24
BAR_H: Final = 3

STATUS_ROW, GAUGE_ROW, DETAILS_ROW = 0, 1, 8
ROW_COUNT: Final = 11


class DetailCells(NamedTuple):
    name: Any
    track: tkinter.Frame
    fill: tkinter.Frame
    watts: Any
    dot: Any
    detail: Any


class MainView:
    """The dashboard, gridded into column 1 of the stage next to the scene."""

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
        self._rules: list[tuple[tkinter.Frame, int]] = []
        self._visible = True
        self._fraction = 0.0
        self._gauge_key: tuple[int, Palette] | None = None
        self._gauge_at: tuple[int, int] | None = None
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
        stage.grid_rowconfigure(GAUGE_ROW, minsize=self._px(GAUGE_ROW_H))
        stage.grid_rowconfigure(DETAILS_ROW, weight=1)

        status = self._row_frame(STATUS_ROW, pady=(L, S))
        status.grid_columnconfigure(1, weight=1)
        self._dot_role = "accent"
        self._status_dot = label(self._painter, status, "●", self._font_caption, self._dot_role)
        self._status_dot.grid(row=0, column=0)
        self._status = label(self._painter, status, "", self._font_body, "text_muted")
        self._status.grid(row=0, column=1, sticky="w", padx=(S, 0))
        self._theme_button = self._hinted_button(status, "", on_toggle_theme)
        self._theme_button.grid(row=0, column=2)
        self._settings_button = self._hinted_button(
            status, self._icons.settings, on_open_settings, "Settings"
        )
        self._settings_button.grid(row=0, column=3, padx=(S, 0))

        readout = self._row_frame(GAUGE_ROW, sticky="n", pady=(WATTS_TOP, 0))
        self._watts = label(self._painter, readout, "", self._font_power, "text")
        self._watts.grid(row=0, column=0, sticky="sw")
        label(self._painter, readout, "W", self._font_unit, "text_muted").grid(
            row=0,
            column=1,
            sticky="sw",
            padx=(XS, 0),
            pady=(0, self._baseline(self._font_power, self._font_unit)),
        )
        self._load = self._on_stage(
            label(self._painter, stage, "", self._font_body, "text_muted"),
            GAUGE_ROW,
            sticky="n",
            pady=(LOAD_TOP, 0),
        )
        ends = self._row_frame(
            GAUGE_ROW, sticky="s", width=GAUGE_W, height=theme.SIZE_CAPTION + XS + 2
        )
        self._zero = label(self._painter, ends, "", self._font_caption, "text_muted")
        self._zero.place(x=ARC_ENDS_X[0], rely=1.0, anchor="s")
        self._peak = label(self._painter, ends, "", self._font_caption, "text_muted")
        self._peak.place(x=ARC_ENDS_X[1], rely=1.0, anchor="sw")

        self._on_stage(self._caption(stage, "SPENT"), 2, sticky="w", pady=(M, 0))
        cost = self._row_frame(3)
        cost.grid_columnconfigure(2, weight=1)
        currency_drop = self._baseline(self._font_cost, self._font_currency)
        label(self._painter, cost, "฿", self._font_currency, "text_muted").grid(
            row=0, column=0, sticky="sw", pady=(0, currency_drop)
        )
        self._spent = label(self._painter, cost, "", self._font_cost, "text")
        self._spent.grid(row=0, column=1, sticky="sw", padx=(S, 0))
        self._spent_caption = label(self._painter, cost, "", self._font_body, "text_muted")
        self._spent_caption.grid(
            row=0, column=3, sticky="se", pady=(0, self._baseline(self._font_cost, self._font_body))
        )

        self._rule(4, L)
        stats = self._row_frame(5, pady=(L, 0))
        for column in (0, 2, 4):
            stats.grid_columnconfigure(column, weight=1, uniform="stat")
        for column in (1, 3):
            line = tkinter.Frame(stats, width=1, bd=0, highlightthickness=0)
            self._painter.paint(line, bg="line")
            line.grid(row=0, column=column, sticky="ns")
        self._per_hour = self._stat(stats, 0, "PER HOUR")
        self._monthly = self._stat(stats, 2, "PER MONTH")
        hours = self._stat_cell(stats, 4, "HOURS A DAY")
        stepper = row(hours)
        stepper.grid(row=1, column=0, sticky="w", pady=(XS, 0))
        self._hours_down = self._stepper_button(stepper, "−", -1)
        self._hours_down.grid(row=0, column=0)
        self._hours_value = ctk.CTkLabel(
            stepper, text="", font=self._font_value, width=28, height=0
        )
        self._painter.paint(self._hours_value, text_color="text")
        self._hours_value.grid(row=0, column=1)
        self._hours_up = self._stepper_button(stepper, "+", 1)
        self._hours_up.grid(row=0, column=2)

        self._rule(6, L)
        self._on_stage(self._caption(stage, "BREAKDOWN"), 7, sticky="w", pady=(L, 0))
        self._details = self._row_frame(DETAILS_ROW, sticky="new", pady=(S, 0))
        self._details.grid_columnconfigure(1, weight=1)

        self._rule(9, M)
        # Ghost buttons pad their text by about S, so the footer is inset by S less on the right
        # to keep Reset's text on the same edge as everything else.
        footer = self._row_frame(10, padx=(PAD[0], PAD[1] - S), pady=(S, L))
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

        self._configure_binding = stage.bind("<Configure>", self._stage_resized, add="+")
        self._destroy_binding = stage.bind("<Destroy>", lambda _e: self._stop(), add="+")
        ctk.ScalingTracker.add_widget(self._rescale, stage)
        self.apply_palette(palette)

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
        if vm.hours_per_day != self._hours:
            self._show_hours(vm.hours_per_day)
        self._render_details(vm.details)
        set_text(self._price, vm.price)

    def apply_palette(self, palette: Palette) -> None:
        self._painter.apply_palette(palette)
        dark = palette == theme.DARK
        set_text(self._theme_button, self._icons.sun if dark else self._icons.moon)
        self._theme_button.hint = "Light mode" if dark else "Dark mode"
        self._show_gauge(self._fraction)

    def hide(self) -> None:
        self._visible = False
        self._disarm_reset()
        self._stage.hide_hint()
        for part in self._parts:
            part.grid_remove()
        self._stage.delete(GAUGE_TAG)
        self._gauge_key = self._gauge_at = None

    def show(self) -> None:
        self._visible = True
        for part in self._parts:
            part.grid()
        self._show_gauge(self._fraction)

    def destroy(self) -> None:
        self._stop()
        self._stage.unbind("<Configure>", self._configure_binding)
        self._stage.unbind("<Destroy>", self._destroy_binding)
        self._stage.delete(GAUGE_TAG)
        for part in self._parts:
            part.destroy()
        self._parts.clear()

    def _px(self, logical: float) -> int:
        return round(logical * self._k)

    def _baseline(self, large: ctk.CTkFont, small: ctk.CTkFont) -> int:
        """Bottom padding that puts `small` text on the same baseline as `large` beside it."""
        drop: int = large.metrics("descent") - small.metrics("descent")
        return round(drop / self._k)

    def _on_stage(self, widget: Any, grid_row: int, **grid: Any) -> Any:
        grid.setdefault("padx", PAD)
        widget.grid(row=grid_row, column=1, **grid)
        if isinstance(widget, ctk.CTkBaseClass):
            self._painter.paint(widget, bg_color="background")
        self._parts.append(widget)
        return widget

    def _row_frame(
        self, grid_row: int, *, sticky: str = "ew", width: int = 0, height: int = 0, **grid: Any
    ) -> Any:
        frame = ctk.CTkFrame(
            self._stage, corner_radius=0, border_width=0, width=width, height=height
        )
        self._painter.paint(frame, fg_color="background")
        return self._on_stage(frame, grid_row, sticky=sticky, **grid)

    def _rule(self, grid_row: int, space_above: int) -> None:
        line = tkinter.Frame(self._stage, height=1, bd=0, highlightthickness=0)
        self._painter.paint(line, bg="line")
        self._on_stage(line, grid_row, sticky="ew", **self._rule_padding(space_above))
        self._rules.append((line, space_above))

    def _rule_padding(self, space_above: int) -> dict[str, Any]:
        # Rules and bars are plain Tk frames, so their padding is scaled here rather than by
        # customtkinter.
        return {
            "padx": tuple(self._px(pad) for pad in PAD),
            "pady": (self._px(space_above), 0),
        }

    def _bar_padding(self, index: int) -> dict[str, Any]:
        return {"padx": (self._px(L), self._px(L)), "pady": (self._px(0 if index == 0 else S), 0)}

    def _caption(self, master: Any, text: str) -> Any:
        return label(self._painter, master, spaced(text), self._font_caption, "text_muted")

    def _stat_cell(self, master: Any, column: int, caption: str) -> Any:
        cell = row(master)
        cell.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else M, M))
        self._caption(cell, caption).grid(row=0, column=0, sticky="w")
        return cell

    def _stat(self, master: Any, column: int, caption: str) -> Any:
        value = label(
            self._painter, self._stat_cell(master, column, caption), "", self._font_value, "text"
        )
        value.grid(row=1, column=0, sticky="w", pady=(XS, 0))
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
        self._fraction = fraction
        if not self._visible:
            return
        lit = round(fraction * (TICKS - 1))
        palette = self._painter.palette
        if (lit, palette) == self._gauge_key:
            return
        self._stage.update_idletasks()
        cell = self._stage.grid_bbox(1, GAUGE_ROW)
        if cell is None:
            return
        self._gauge_key = (lit, palette)
        image = render_gauge(
            lit / (TICKS - 1), palette.accent, palette.track, self._px(GAUGE_W), self._px(GAUGE_H)
        )
        x, y, width, _ = cell
        left, right = (self._px(pad) for pad in PAD)
        at = (x + left + (width - left - right - image.width) // 2, y)
        self._stage.place_image(GAUGE_TAG, image, *at)
        if at != self._gauge_at:
            self._gauge_at = at
            self._stage.request_backdrop()

    def _stage_resized(self, _event: "tkinter.Event[tkinter.Misc]") -> None:
        self._redraw_gauge_later()

    def _redraw_gauge_later(self) -> None:
        self._gauge_key = None
        self._stage.after_idle(lambda: self._show_gauge(self._fraction))

    def _rescale(self, widget_scaling: float, _window_scaling: float) -> None:
        self._k = widget_scaling
        self._stage.grid_rowconfigure(GAUGE_ROW, minsize=self._px(GAUGE_ROW_H))
        for line, space_above in self._rules:
            line.grid_configure(**self._rule_padding(space_above))
        for index, cells in enumerate(self._detail_cells):
            cells.track.configure(height=self._px(BAR_H))
            cells.track.grid_configure(**self._bar_padding(index))
        self._redraw_gauge_later()

    def _render_details(self, rows: tuple[DetailRow, ...]) -> None:
        if [r.label for r in rows] != [cells.name.cget("text") for cells in self._detail_cells]:
            self._rebuild_detail_rows([r.label for r in rows])
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
            for widget in (cells.name, cells.track, cells.watts, cells.dot, cells.detail):
                widget.destroy()
        self._detail_cells = []
        for i, name in enumerate(names):
            pady = (0 if i == 0 else S, 0)
            name_label = label(self._painter, self._details, name, self._font_body, "text")
            name_label.grid(row=i, column=0, sticky="w", pady=pady)
            track = tkinter.Frame(self._details, height=self._px(BAR_H), bd=0, highlightthickness=0)
            self._painter.paint(track, bg="track")
            track.grid(row=i, column=1, sticky="ew", **self._bar_padding(i))
            fill = tkinter.Frame(track, bd=0, highlightthickness=0)
            self._painter.paint(fill, bg="text_muted")
            power = label(self._painter, self._details, "", self._font_body, "text")
            power.grid(row=i, column=2, sticky="e", pady=pady)
            dot = label(self._painter, self._details, "", self._font_caption, "text_muted")
            dot.grid(row=i, column=3, padx=(M, XS), pady=pady)
            detail = label(self._painter, self._details, "", self._font_caption, "text_muted")
            detail.grid(row=i, column=4, sticky="w", pady=pady)
            self._detail_cells.append(DetailCells(name_label, track, fill, power, dot, detail))

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
        if self._reset_job is not None:
            self._stage.after_cancel(self._reset_job)
            self._reset_job = None

    def _end_reset_confirm(self) -> None:
        self._reset_job = None
        set_text(self._reset_button, "Reset")
        self._painter.paint(self._reset_button, text_color="text_muted")
