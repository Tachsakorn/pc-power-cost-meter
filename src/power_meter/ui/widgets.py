import tkinter
from collections.abc import Callable
from typing import Any, Final

import customtkinter as ctk

from power_meter.ui import theme
from power_meter.ui.theme import Palette

FOCUS_RING_W: Final = 2


class Painter:
    """Colours widgets from palette roles (e.g. text_color="text_muted") and remembers them, so a
    new palette can recolour everything live."""

    def __init__(self, palette: Palette) -> None:
        self.palette = palette
        self._roles: dict[Any, dict[str, str]] = {}

    def paint(self, widget: Any, **roles: str) -> None:
        self._roles.setdefault(widget, {}).update(roles)
        self._apply(widget)

    def forget(self, widget: Any) -> None:
        del self._roles[widget]

    def apply_palette(self, palette: Palette) -> None:
        self.palette = palette
        for widget in self._roles:
            self._apply(widget)

    def _apply(self, widget: Any) -> None:
        roles = self._roles[widget]
        widget.configure(**{option: getattr(self.palette, role) for option, role in roles.items()})


class Button(ctk.CTkButton):
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


def row(master: Any) -> Any:
    return ctk.CTkFrame(master, corner_radius=0, fg_color="transparent")


def set_text(widget: Any, text: str) -> None:
    if widget.cget("text") != text:
        widget.configure(text=text)


def label(painter: Painter, master: Any, text: str, font: Any, role: str) -> Any:
    widget = ctk.CTkLabel(master, text=text, font=font, anchor="w", justify="left", height=0)
    painter.paint(widget, text_color=role)
    return widget


def ghost_button(
    painter: Painter,
    master: Any,
    text: str,
    command: Callable[[], None],
    font: Any,
    text_role: str = "text_muted",
) -> Button:
    button = Button(master, text=text, command=command, font=font, width=0, height=28)
    painter.paint(
        button,
        fg_color="background",
        hover_color="surface",
        text_color=text_role,
        border_color="focus",
    )
    return button
