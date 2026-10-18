import tkinter
from collections.abc import Callable, Mapping
from typing import Any, Final, Literal

import customtkinter as ctk

from power_meter import __version__
from power_meter.sensors.base import Component
from power_meter.settings import STANDARD_BAND_RATES, MeterType, Settings, StandardBand
from power_meter.ui import theme
from power_meter.ui.form import (
    HARDWARE_FIELDS,
    RATE_FIELDS,
    active_fields,
    form_from_settings,
    settings_from_form,
)
from power_meter.ui.theme import Palette
from power_meter.ui.widgets import (
    CONTROL_H,
    Button,
    Dropdown,
    Field,
    Painter,
    ScrollArea,
    Segmented,
    ghost_button,
    label,
    row,
    set_text,
)

type Theme = Literal["dark", "light"]

XS, S, M, L, XL = theme.SPACE

METER_OPTIONS: Final = ((MeterType.STANDARD, "Normal"), (MeterType.TOU, "TOU"))
THEME_OPTIONS: Final[tuple[tuple[Theme, str], ...]] = (("dark", "Dark"), ("light", "Light"))
BAND_OPTIONS: Final = tuple(
    (band, f"{text}฿{STANDARD_BAND_RATES[band]:.2f}")
    for band, text in (
        (StandardBand.UNKNOWN, "I don't know — use "),
        (StandardBand.UP_TO_200, "Up to 200 units a month — "),
        (StandardBand.BAND_201_400, "201–400 units a month — "),
        (StandardBand.OVER_400, "Over 400 units a month — "),
    )
) + ((StandardBand.CUSTOM, "Custom price…"),)

PER_UNIT: Final = "฿ per unit"
RATE_LABELS: Final = {
    "custom_rate": ("Your price", "Your bill total divided by units used", PER_UNIT),
    "tou_peak_rate": ("Peak", "Mon–Fri 09:00–22:00", PER_UNIT),
    "tou_offpeak_rate": ("Off-peak", "Nights, weekends and public holidays", PER_UNIT),
}
HARDWARE_LABELS: Final = {
    "other_w": ("Other components", "Motherboard, memory, drives and fans", "W"),
    "extra_w": ("Monitor & extras", "Screens, speakers and other add-ons", "W"),
    "psu_efficiency": (
        "Power supply efficiency",
        "Share of wall power that reaches the parts",
        "%",
    ),
    "cpu_tdp_w": ("CPU TDP", "Used when CPU power can't be measured", "W"),
    "gpu_tdp_w": ("GPU TDP", "Used when GPU power can't be measured", "W"),
}
TDP_FIELDS: Final[Mapping[Component, str]] = {"cpu": "cpu_tdp_w", "gpu": "gpu_tdp_w"}
DROPDOWN_W: Final = 280
CONTENT_W: Final = 460 - 2 * XL  # the window is 460 px wide


class SettingsView(ctk.CTkFrame):
    def __init__(
        self,
        master: Any,
        palette: Palette,
        settings: Settings,
        data_dir_text: str,
        measured: Mapping[Component, bool],
        on_save: Callable[[Settings], None],
        on_cancel: Callable[[], None],
        on_theme_change: Callable[[Theme], None],
    ) -> None:
        super().__init__(master, corner_radius=0)
        self._painter = Painter(palette)
        self._base = settings
        self._on_save = on_save
        self._on_cancel = on_cancel
        self._on_theme_change = on_theme_change
        self._meter_type = settings.meter_type
        self._band = settings.standard_band
        self._theme: Theme = settings.theme

        self._font_body = ctk.CTkFont(theme.FONT_FAMILY, theme.SIZE_BODY)
        self._font_strong = ctk.CTkFont(theme.FONT_FAMILY, theme.SIZE_BODY, "bold")
        self._font_caption = ctk.CTkFont(theme.FONT_FAMILY, theme.SIZE_CAPTION)
        fonts = {"font": self._font_body, "caption_font": self._font_caption}

        form = form_from_settings(settings)
        self._vars = {
            name: tkinter.StringVar(self, form[name]) for name in RATE_FIELDS + HARDWARE_FIELDS
        }

        self._painter.paint(self, fg_color="background")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # The same height as the main view's header row, so the title doesn't jump between views.
        header = label(self._painter, self, "Settings", self._font_strong, "text")
        header.configure(height=28)
        header.grid(row=0, column=0, sticky="ew", padx=XL, pady=(L, 0))

        self._body = ScrollArea(self, self._painter)
        self._body.grid(row=1, column=0, sticky="nsew", pady=(S, 0))
        content = self._body.content
        content.grid_columnconfigure(0, weight=1)

        self._caption(content, "Your electricity meter").grid(row=0, column=0, sticky="w", padx=XL)
        self._meter = Segmented(
            content, self._painter, self._font_body, METER_OPTIONS, self._meter_chosen
        )
        self._meter.set(self._meter_type)
        self._meter.grid(row=1, column=0, sticky="w", padx=XL, pady=(XS, 0))

        self._caption(content, "Price per unit").grid(
            row=2, column=0, sticky="w", padx=XL, pady=(L, 0)
        )
        price = row(content)
        price.grid(row=3, column=0, sticky="ew", padx=XL, pady=(XS, 0))
        price.grid_columnconfigure(0, weight=1)
        self._band_menu = Dropdown(
            price, self._painter, self._font_body, BAND_OPTIONS, self._band_chosen, DROPDOWN_W
        )
        self._band_menu.set(self._band)
        self._band_menu.grid(row=0, column=0, sticky="w")
        self._hint = label(
            self._painter,
            price,
            'Find "units used" on your electricity bill',
            self._font_caption,
            "text_muted",
        )
        self._hint.grid(row=1, column=0, sticky="w", pady=(XS, 0))

        self._fields = {
            name: Field(price, self._painter, title, caption, unit, self._vars[name], **fonts)
            for name, (title, caption, unit) in RATE_LABELS.items()
        }
        self._fields["custom_rate"].grid(row=2, column=0, sticky="ew", pady=(M, 0))
        self._fields["tou_peak_rate"].grid(row=3, column=0, sticky="ew")
        self._fields["tou_offpeak_rate"].grid(row=4, column=0, sticky="ew", pady=(M, 0))

        self._caption(content, "Theme").grid(row=4, column=0, sticky="w", padx=XL, pady=(L, 0))
        self._theme_choice = Segmented(
            content, self._painter, self._font_body, THEME_OPTIONS, self._theme_chosen
        )
        self._theme_choice.set(self._theme)
        self._theme_choice.grid(row=5, column=0, sticky="w", padx=XL, pady=(XS, 0))

        # Ghost buttons pad their text by about S; the inset keeps it on the content edge.
        self._advanced_toggle = ghost_button(
            self._painter, content, "▸ Advanced settings", self._toggle_advanced, self._font_body
        )
        self._advanced_toggle.grid(row=6, column=0, sticky="w", padx=XL - S, pady=(L, 0))
        self._advanced = row(content)
        self._advanced.grid_columnconfigure(0, weight=1)
        self._advanced.grid(row=7, column=0, sticky="ew", padx=XL, pady=(XS, 0))
        for i, (name, (title, caption, unit)) in enumerate(HARDWARE_LABELS.items()):
            field = Field(
                self._advanced, self._painter, title, caption, unit, self._vars[name], **fonts
            )
            field.grid(row=i, column=0, sticky="ew", pady=(0 if i == 0 else M, 0))
            self._fields[name] = field
        for component, name in TDP_FIELDS.items():
            if measured.get(component, False):
                self._fields[name].disable("measured — not used")
        folder = len(HARDWARE_LABELS)
        label(self._painter, self._advanced, "Data folder", self._font_body, "text").grid(
            row=folder, column=0, sticky="w", pady=(M, 0)
        )
        path = label(self._painter, self._advanced, data_dir_text, self._font_caption, "text_muted")
        path.configure(wraplength=CONTENT_W)
        path.grid(row=folder + 1, column=0, sticky="w")
        self._advanced.grid_remove()

        label(
            self._painter, content, f"Version {__version__}", self._font_caption, "text_muted"
        ).grid(row=8, column=0, sticky="w", padx=XL, pady=(L, M))

        footer = row(self)
        footer.grid(row=2, column=0, sticky="ew", padx=XL, pady=(M, L))
        footer.grid_columnconfigure(0, weight=1)
        self._status = label(self._painter, footer, "", self._font_caption, "error")
        self._status.grid(row=0, column=0, sticky="w")
        ghost_button(self._painter, footer, "Cancel", self._cancel, self._font_body).grid(
            row=0, column=1
        )
        self._save_button = Button(
            footer,
            text="Save",
            command=self._save,
            font=self._font_strong,
            width=88,
            height=CONTROL_H - XS,
        )
        self._painter.paint(
            self._save_button,
            fg_color="surface",
            hover_color="background",
            text_color="accent_power",
            text_color_disabled="text_muted",
            border_color="focus",
        )
        self._save_button.grid(row=0, column=2, padx=(S, 0))

        for var in self._vars.values():
            var.trace_add("write", lambda *_: self._validate())
        top = self.winfo_toplevel()
        self._key_bindings = [
            ("<Escape>", top.bind("<Escape>", lambda _e: self._cancel(), add="+")),
            ("<Return>", top.bind("<Return>", self._enter, add="+")),
        ]

        self._show_price_fields()
        self._validate()

    def apply_palette(self, palette: Palette) -> None:
        self._painter.apply_palette(palette)

    def destroy(self) -> None:
        top = self.winfo_toplevel()
        for sequence, funcid in self._key_bindings:
            top.unbind(sequence, funcid)
        super().destroy()

    def _caption(self, master: Any, text: str) -> Any:
        return label(self._painter, master, text, self._font_body, "text_muted")

    def _form(self) -> dict[str, str]:
        return {
            "meter_type": self._meter_type.value,
            "standard_band": self._band.value,
            "theme": self._theme,
        } | {name: var.get() for name, var in self._vars.items()}

    def _validate(self) -> None:
        _, errors = settings_from_form(self._base, self._form())
        for name, field in self._fields.items():
            field.show_error(errors.get(name))
        self._save_button.configure(state="disabled" if errors else "normal")
        if not errors:
            set_text(self._status, "")
        elif len(errors) == 1:
            set_text(self._status, "1 field needs fixing")
        else:
            set_text(self._status, f"{len(errors)} fields need fixing")

    def _show_price_fields(self) -> None:
        shown = active_fields(self._meter_type, self._band)
        for name in RATE_FIELDS:
            if name in shown:
                self._fields[name].grid()
            else:
                self._fields[name].grid_remove()
        for widget in (self._band_menu, self._hint):
            if self._meter_type is MeterType.STANDARD:
                widget.grid()
            else:
                widget.grid_remove()

    def _meter_chosen(self, meter_type: MeterType) -> None:
        self._meter_type = meter_type
        self._show_price_fields()
        self._validate()

    def _band_chosen(self, band: StandardBand) -> None:
        self._band = band
        self._show_price_fields()
        self._validate()
        if band is StandardBand.CUSTOM:
            self._fields["custom_rate"].entry.focus_set()

    def _theme_chosen(self, value: Theme) -> None:
        self._theme = value
        self._on_theme_change(value)

    def _toggle_advanced(self) -> None:
        if self._advanced.winfo_manager():
            self._advanced.grid_remove()
            set_text(self._advanced_toggle, "▸ Advanced settings")
            return
        self._advanced.grid()
        set_text(self._advanced_toggle, "▾ Advanced settings")
        self.update_idletasks()
        self._body.reveal(self._advanced)

    def _enter(self, event: Any) -> None:
        # Enter on a button or the band menu belongs to that control; elsewhere it means Save.
        # This reacts to the key press, so the release of an Enter that picked a menu item can't
        # save the form.
        if isinstance(event.widget, tkinter.Entry) or event.widget is self.winfo_toplevel():
            self._save()

    def _save(self) -> None:
        settings, _ = settings_from_form(self._base, self._form())
        if settings is not None:
            self._on_save(settings)

    def _cancel(self) -> None:
        if self._theme != self._base.theme:
            self._on_theme_change(self._base.theme)
        self._on_cancel()
