import tkinter
import tkinter.font
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any, Final, NamedTuple

import customtkinter as ctk

from power_meter.ui import theme
from power_meter.ui.theme import Palette

XS, S, M, L, XL = theme.SPACE
FOCUS_RING_W: Final = 2
CONTROL_H: Final = 36
UNIT_W: Final = 64
ICON_BUTTON: Final = 32


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
    """A CTkButton that can be reached with Tab, shows a focus ring and responds to Enter/Space.

    `outline` is the border width when unfocused. `tooltip` names a button whose text is an
    icon glyph."""

    def __init__(self, master: Any, *, outline: int = 0, tooltip: str = "", **kwargs: Any) -> None:
        super().__init__(master, border_width=outline, corner_radius=theme.RADIUS, **kwargs)
        self.tooltip = tooltip
        # CTkButton forwards only `cursor` to its frame. Making the frame itself focusable
        # gives one Tab stop per button instead of stops on its inner canvas and label.
        tkinter.Frame.configure(self, takefocus=1)
        tkinter.Misc.bind(self, "<FocusIn>", lambda _e: self.configure(border_width=FOCUS_RING_W))
        tkinter.Misc.bind(self, "<FocusOut>", lambda _e: self.configure(border_width=outline))
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


def icon_button(
    painter: Painter,
    master: Any,
    glyph: str,
    command: Callable[[], None],
    font: Any,
    tooltip: str,
    size: int = ICON_BUTTON,
) -> Button:
    button = Button(
        master,
        text=glyph,
        command=command,
        font=font,
        width=size,
        height=size,
        outline=1,
        tooltip=tooltip,
    )
    painter.paint(
        button,
        fg_color="surface",
        hover_color="track",
        text_color="text",
        # Inactive controls are exempt from the text contrast minimum; the faint glyph is what
        # shows they are inactive.
        text_color_disabled="text_faint",
        border_color="line",
    )
    tkinter.Misc.bind(
        button, "<FocusIn>", lambda _e: painter.paint(button, border_color="focus"), add="+"
    )
    tkinter.Misc.bind(
        button, "<FocusOut>", lambda _e: painter.paint(button, border_color="line"), add="+"
    )
    return button


class Icons(NamedTuple):
    family: str
    sun: str
    moon: str
    settings: str


def pick_icons(master: tkinter.Misc) -> Icons:
    """Windows' icon font when it is installed, otherwise text symbols in the body font."""
    installed = set(tkinter.font.families(master))
    for family in theme.ICON_FONTS:
        if family in installed:
            return Icons(family, "\ue706", "\ue708", "\ue713")
    return Icons(theme.FONT_FAMILY, "☀", "☾", "⚙")


class Segmented[T](ctk.CTkFrame):
    """Mutually exclusive options side by side. Each option is a Button, so it is its own Tab
    stop and Enter/Space choose it."""

    def __init__(
        self,
        master: Any,
        painter: Painter,
        font: Any,
        options: Sequence[tuple[T, str]],
        command: Callable[[T], None],
        segment_width: int = 96,
    ) -> None:
        super().__init__(master, corner_radius=theme.RADIUS)
        self._painter = painter
        self._command = command
        self._buttons: dict[T, Button] = {}
        self.value: T | None = None
        painter.paint(self, fg_color="surface")
        last = len(options) - 1
        for column, (value, text) in enumerate(options):
            button = Button(
                self,
                text=text,
                font=font,
                width=segment_width,
                height=CONTROL_H - 2 * XS,
                command=partial(self._choose, value),
            )
            button.grid(row=0, column=column, padx=(XS, XS if column == last else 0), pady=XS)
            self._buttons[value] = button

    def set(self, value: T) -> None:
        self.value = value
        for option, button in self._buttons.items():
            if option == value:
                # The focus ring takes the text colour here: the usual focus colour is the fill.
                self._painter.paint(
                    button,
                    fg_color="accent",
                    hover_color="accent",
                    text_color="background",
                    border_color="text",
                )
            else:
                self._painter.paint(
                    button,
                    fg_color="surface",
                    hover_color="background",
                    text_color="text",
                    border_color="focus",
                )

    def _choose(self, value: T) -> None:
        if value != self.value:
            self.set(value)
            self._command(value)


class Dropdown[T](ctk.CTkFrame):
    """Shows the chosen option and opens a menu of all of them on click, Enter, Space or Down.

    CTkOptionMenu can't take keyboard focus, which is why this exists."""

    def __init__(
        self,
        master: Any,
        painter: Painter,
        font: Any,
        options: Sequence[tuple[T, str]],
        command: Callable[[T], None],
        width: int,
    ) -> None:
        super().__init__(master, width=width, height=CONTROL_H, corner_radius=theme.RADIUS)
        self._painter = painter
        self._command = command
        self._choices = list(options)
        self._index = tkinter.IntVar(self, -1)
        self.grid_propagate(False)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        tkinter.Frame.configure(self, takefocus=1)

        self._text = label(painter, self, "", font, "text")
        self._text.grid(row=0, column=0, sticky="w", padx=(M, 0))
        chevron = label(painter, self, "▾", font, "text_muted")
        chevron.grid(row=0, column=1, padx=M)

        self._menu = tkinter.Menu(self, tearoff=False, font=font, relief="flat", borderwidth=0)
        for index, (value, text) in enumerate(self._choices):
            self._menu.add_radiobutton(
                label=text,
                variable=self._index,
                value=index,
                command=partial(self._choose, value),
            )
        painter.paint(
            self._menu,
            bg="surface",
            fg="text",
            activebackground="accent",
            activeforeground="background",
            selectcolor="text",
        )
        painter.paint(self, fg_color="surface", border_color="focus")

        for part in (self, self._text, chevron):
            part.bind("<Button-1>", lambda _e: self._open())
            part.bind("<Enter>", lambda _e: self._painter.paint(self, fg_color="background"))
            part.bind("<Leave>", lambda _e: self._painter.paint(self, fg_color="surface"))
        tkinter.Misc.bind(self, "<FocusIn>", lambda _e: self.configure(border_width=FOCUS_RING_W))
        tkinter.Misc.bind(self, "<FocusOut>", lambda _e: self.configure(border_width=0))
        # On press, unlike Button: the menu takes the keyboard while open, and the release of
        # the key that picked an item must not open it again.
        for key in ("<KeyPress-Return>", "<KeyPress-space>", "<KeyPress-Down>"):
            tkinter.Misc.bind(self, key, lambda _e: self._open())

    def set(self, value: T) -> None:
        index = [option for option, _ in self._choices].index(value)
        self._index.set(index)
        set_text(self._text, self._choices[index][1])

    def _choose(self, value: T) -> None:
        self.set(value)
        self._command(value)

    def _open(self) -> None:
        self.focus_set()
        self._menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + XS)


class Field(ctk.CTkFrame):
    """A labelled number box with its unit, a caption under the label and room for an error."""

    def __init__(
        self,
        master: Any,
        painter: Painter,
        title: str,
        caption: str,
        unit: str,
        variable: tkinter.StringVar,
        *,
        font: Any,
        caption_font: Any,
    ) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._painter = painter
        self._focused = False
        self._error: str | None = None
        self.grid_columnconfigure(0, weight=1)

        label(painter, self, title, font, "text").grid(row=0, column=0, sticky="sw")
        self._caption = label(painter, self, caption, caption_font, "text_muted")
        self._caption.grid(row=1, column=0, sticky="nw")
        self.entry = ctk.CTkEntry(
            self,
            textvariable=variable,
            width=80,
            height=CONTROL_H,
            corner_radius=theme.RADIUS,
            font=font,
            justify="right",
        )
        self.entry.grid(row=0, column=1, rowspan=2, padx=(M, 0))
        unit_label = ctk.CTkLabel(self, text=unit, font=font, width=UNIT_W, anchor="w")
        painter.paint(unit_label, text_color="text_muted")
        unit_label.grid(row=0, column=2, rowspan=2, padx=(S, 0))
        self._message = label(painter, self, "", caption_font, "error")
        self._message.grid(row=2, column=0, columnspan=3, sticky="w", pady=(XS, 0))
        self._message.grid_remove()

        self.entry.bind("<FocusIn>", lambda _e: self._set_focused(True))
        self.entry.bind("<FocusOut>", lambda _e: self._set_focused(False))
        self._restyle()

    def show_error(self, message: str | None) -> None:
        if message == self._error:
            return
        self._error = message
        if message is None:
            self._message.grid_remove()
        else:
            set_text(self._message, message)
            self._message.grid()
        self._restyle()

    def disable(self, caption: str) -> None:
        self.entry.configure(state="disabled")
        set_text(self._caption, caption)
        self._restyle()

    def _set_focused(self, focused: bool) -> None:
        self._focused = focused
        self._restyle()

    def _restyle(self) -> None:
        if self.entry.cget("state") == "disabled":
            self._painter.paint(
                self.entry, fg_color="background", border_color="surface", text_color="text_muted"
            )
            self.entry.configure(border_width=1)
            return
        border = "error" if self._error else "focus" if self._focused else "text_muted"
        self._painter.paint(self.entry, fg_color="surface", border_color=border, text_color="text")
        self.entry.configure(border_width=FOCUS_RING_W if self._error or self._focused else 1)


class ScrollArea(ctk.CTkFrame):
    """A vertically scrolling area. It asks for the full height of its content, and scrolls only
    when given less; the scrollbar shows only then, and the focused widget is kept in view so
    Tab never lands on something hidden."""

    def __init__(self, master: Any, painter: Painter) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._viewport = tkinter.Canvas(
            self, highlightthickness=0, borderwidth=0, yscrollincrement=1
        )
        self._scrollbar = ctk.CTkScrollbar(self, width=S + XS, command=self._viewport.yview)
        self.content = ctk.CTkFrame(self._viewport, corner_radius=0)
        self._window = self._viewport.create_window(0, 0, window=self.content, anchor="nw")
        self._viewport.configure(yscrollcommand=self._scrollbar.set)
        painter.paint(self._viewport, bg="background")
        # The content's master is a plain canvas, so CTk can't work out its bg_color itself.
        painter.paint(self.content, fg_color="background", bg_color="background")
        painter.paint(
            self._scrollbar,
            fg_color="background",
            button_color="text_muted",
            button_hover_color="text",
        )
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._viewport.grid(row=0, column=0, sticky="nsew")

        tkinter.Misc.bind(self.content, "<Configure>", lambda _e: self._fit(), add="+")
        self._viewport.bind("<Configure>", self._viewport_resized)
        # Wheel and focus events reach the toplevel from every widget inside it, which saves
        # binding each child; the handlers ignore events from outside this area.
        top = self.winfo_toplevel()
        self._bindings = [
            (sequence, top.bind(sequence, handler, add="+"))
            for sequence, handler in (("<MouseWheel>", self._wheel), ("<FocusIn>", self._focus_in))
        ]

    def reveal(self, widget: Any) -> None:
        # Content that has just grown hasn't sent its <Configure> yet; without this the old
        # scroll region would clamp the move.
        self._fit()
        if not self._overflowing():
            return
        top = widget.winfo_rooty() - self.content.winfo_rooty() - L
        bottom = top + widget.winfo_height() + 2 * L
        view_top = self._viewport.canvasy(0)
        view_height = self._viewport.winfo_height()
        if top < view_top:
            target = top
        elif bottom > view_top + view_height:
            target = min(bottom - view_height, top)
        else:
            return
        self._viewport.yview_moveto(max(target, 0) / self.content.winfo_reqheight())

    def destroy(self) -> None:
        top = self.winfo_toplevel()
        for sequence, funcid in self._bindings:
            top.unbind(sequence, funcid)
        super().destroy()

    def _viewport_resized(self, event: Any) -> None:
        self._viewport.itemconfigure(self._window, width=event.width)
        self._fit()

    def _fit(self) -> None:
        height = self.content.winfo_reqheight()
        self._viewport.configure(height=height, scrollregion=(0, 0, 0, height))
        # Placed over the right gutter rather than gridded, so showing it doesn't reflow the
        # content.
        if self._overflowing():
            self._scrollbar.place(relx=1, x=-XS, relheight=1, anchor="ne")
        else:
            self._scrollbar.place_forget()
            self._viewport.yview_moveto(0)

    def _overflowing(self) -> bool:
        # A canvas that hasn't been laid out yet reports a height of 1.
        return bool(self.content.winfo_reqheight() > self._viewport.winfo_height() > 1)

    def _owns(self, widget: Any) -> bool:
        return str(widget).startswith(str(self) + ".")

    def _wheel(self, event: Any) -> None:
        if self._overflowing() and self._owns(event.widget):
            # Windows reports 120 per notch; this scrolls 40 px per notch.
            self._viewport.yview_scroll(int(-event.delta / 3), "units")

    def _focus_in(self, event: Any) -> None:
        if self._owns(event.widget):
            self.reveal(event.widget)
