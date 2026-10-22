import random
import tkinter
from typing import Final, Literal, NamedTuple

import customtkinter as ctk
from PIL import Image, ImageTk

from power_meter.ui.scene.art import NATIVE, SceneArt
from power_meter.ui.scene.backdrop import CROSS_ARM, CROSS_CENTRE, Backdrop, Rect, render_backdrop
from power_meter.ui.scene.state import SceneState
from power_meter.ui.theme import FONT_FAMILY, SIZE_CAPTION, Palette

TICK_MS: Final = 125
TWINKLE_EVERY: Final = 8
TWINKLES_PER_STEP: Final = 2
WINDOW: Final = (760, 600)
SCENE_COLUMN: Final = 360
# The power ring around the dome, in the art's native pixels: its centre, a radius that clears
# the dome's knob and the rim of its base, and the width the ring needs with its ticks. The
# scene is drawn at the largest whole scale that fits that width in the scene column.
RING_CENTRE_NATIVE: Final = (64, 82)
RING_RADIUS_NATIVE: Final = 72
RING_SPAN_NATIVE: Final = 168
RING_CENTRE_Y: Final = 256
TOOLTIP_GAP: Final = 8
TOOLTIP_PAD: Final = (8, 4)
TOOLTIP_MARGIN: Final = 4
HINT_GAP: Final = 6

STAR_BRIGHT: Final = "#{:02X}{:02X}{:02X}".format(*CROSS_CENTRE)
STAR_DIM: Final = "#{:02X}{:02X}{:02X}".format(*CROSS_ARM)


class Ring(NamedTuple):
    x: int
    y: int
    radius: int


class Stage(tkinter.Canvas):
    """Window-filling canvas: pixel backdrop and the animated dome scene.

    Views are gridded straight onto the stage, right of the scene column; call
    `request_backdrop` after the layout changes so the decorations keep clear of them.
    """

    def __init__(
        self,
        master: tkinter.Misc,
        palette: Palette,
        daylight: bool,
        art: SceneArt,
    ) -> None:
        super().__init__(master, highlightthickness=0, bd=0, bg=palette.background)
        self._art = art
        self._k: float = ctk.ScalingTracker.get_widget_scaling(self)
        self._fit_scene()

        self._palette = palette
        self._daylight = daylight
        self._state = SceneState.DORMANT
        self._tooltip = ""
        self._hovering = False
        self._animate = True
        self._started = False
        self._tick = 0
        self._job: str | None = None
        self._backdrop_job: str | None = None
        self._rng = random.Random()
        self._dimmed: set[int] = set()
        self._size = (0, 0)
        self._backdrop: Backdrop | None = None
        self._backdrop_photo: ImageTk.PhotoImage | None = None
        self._scene_photo: ImageTk.PhotoImage | None = None
        self._scene_base: Image.Image | None = None
        self._placed: dict[str, ImageTk.PhotoImage] = {}

        self._toplevel = self.winfo_toplevel()
        self._toplevel_bindings = [
            ("<Unmap>", self._toplevel.bind("<Unmap>", self._on_unmap, add="+")),
            ("<Map>", self._toplevel.bind("<Map>", self._on_map, add="+")),
        ]
        self.bind("<Configure>", self._on_configure)
        self.tag_bind("scene", "<Enter>", self._on_scene_enter)
        self.tag_bind("scene", "<Leave>", self._on_scene_leave)
        ctk.ScalingTracker.add_widget(self._rescale, self)

    def set_theme(self, palette: Palette, daylight: bool) -> None:
        self._palette = palette
        self._daylight = daylight
        self._scene_base = None
        self.configure(bg=palette.background)
        if self._hovering:
            self._draw_tooltip()
        self.request_backdrop()

    def request_backdrop(self) -> None:
        """Redraws the backdrop and scene once the current event is handled; requests made
        before then share that one redraw. Nothing is drawn until the stage has started."""
        # A timer rather than after_idle: views call update_idletasks() while they lay
        # themselves out, which would run an idle redraw before they had finished.
        if self._started and self._backdrop_job is None:
            self._backdrop_job = self.after(0, self._refresh_backdrop)

    def show_hint(self, text: str, widget: tkinter.Misc) -> None:
        """Shows `text` under `widget`, right-aligned with it."""
        right = widget.winfo_rootx() - self.winfo_rootx() + widget.winfo_width()
        bottom = widget.winfo_rooty() - self.winfo_rooty() + widget.winfo_height()
        pad_x, pad_y = self._label_pad()
        self._draw_label(
            "hint", text, right - pad_x, bottom + round(HINT_GAP * self._k) + pad_y, "ne"
        )

    def hide_hint(self) -> None:
        self.delete("hint")

    def place_image(self, tag: str, image: Image.Image, x: int, y: int) -> None:
        """Shows `image` above the scene. It takes no pointer events, so the scene under its
        transparent parts still shows its hover text."""
        photo = self._placed.get(tag)
        exists = bool(self.find_withtag(tag))
        if exists and photo is not None and (photo.width(), photo.height()) == image.size:
            photo.paste(image)
            self.coords(tag, x, y)
            return
        photo = ImageTk.PhotoImage(image)
        self._placed[tag] = photo
        if exists:
            self.itemconfigure(tag, image=photo)
            self.coords(tag, x, y)
        else:
            self.create_image(x, y, anchor="nw", image=photo, tags=tag, state="disabled")

    def show_scene(self, state: SceneState, tooltip: str) -> None:
        changed = state is not self._state
        self._state = state
        self._tooltip = tooltip
        if self._hovering:
            self._draw_tooltip()
        if changed and self._started and not self._animate:
            self._draw_scene()

    def set_animate(self, on: bool) -> None:
        if on == self._animate:
            return
        self._animate = on
        if on:
            self._schedule()
            return
        self._cancel()
        self.itemconfigure("twinkler", fill=STAR_BRIGHT)
        self._dimmed.clear()
        if self._started:
            self._draw_scene()

    def start(self) -> None:
        self._started = True
        self.request_backdrop()
        self._schedule()

    def destroy(self) -> None:
        self._cancel()
        if self._backdrop_job is not None:
            self.after_cancel(self._backdrop_job)
            self._backdrop_job = None
        ctk.ScalingTracker.remove_widget(self._rescale, self)
        for sequence, funcid in self._toplevel_bindings:
            self._toplevel.unbind(sequence, funcid)
        self._toplevel_bindings.clear()
        super().destroy()

    def _fit_scene(self) -> None:
        """Sizes the scene to whole pixels that leave room for the ring in the scene column,
        and centres the ring in that column."""
        self.scene_column_px = round(SCENE_COLUMN * self._k)
        self.configure(width=self.scene_column_px, height=round(WINDOW[1] * self._k))
        self.grid_columnconfigure(0, minsize=self.scene_column_px)
        scale = self.pixel_size = max(1, self.scene_column_px // RING_SPAN_NATIVE)
        self.ring = Ring(
            self.scene_column_px // 2, round(RING_CENTRE_Y * self._k), RING_RADIUS_NATIVE * scale
        )
        left = self.ring.x - RING_CENTRE_NATIVE[0] * scale
        top = self.ring.y - RING_CENTRE_NATIVE[1] * scale
        self._scene_box: Rect = (left, top, left + NATIVE[0] * scale, top + NATIVE[1] * scale)
        self._scene_base = None

    @property
    def scene_bottom(self) -> int:
        """The bottom edge of the dome's base on the stage."""
        return self._scene_box[1] + self._art.bottom * self.pixel_size

    def _rescale(self, widget_scaling: float, _window_scaling: float) -> None:
        if widget_scaling == self._k:
            return
        self._k = widget_scaling
        self._fit_scene()
        self.delete("scene", "hint", "tooltip")
        self._scene_photo = None
        self._hovering = False
        self.request_backdrop()

    def _refresh_backdrop(self) -> None:
        self.update_idletasks()
        self._backdrop_job = None
        width, height = self.winfo_width(), self.winfo_height()
        if width > 1 and height > 1:
            self._render_backdrop(width, height)
        self._draw_scene()

    def _schedule(self) -> None:
        if self._job is None and self._animate and self._started:
            self._job = self.after(TICK_MS, self._advance)

    def _cancel(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _advance(self) -> None:
        self._job = None
        self._tick += 1
        self._draw_scene()
        if self._tick % TWINKLE_EVERY == 0:
            self._twinkle()
        self._schedule()

    def _on_unmap(self, event: "tkinter.Event[tkinter.Misc]") -> None:
        if event.widget is self._toplevel:
            self._cancel()

    def _on_map(self, event: "tkinter.Event[tkinter.Misc]") -> None:
        if event.widget is self._toplevel:
            self._schedule()

    def _on_configure(self, event: "tkinter.Event[tkinter.Misc]") -> None:
        if (event.width, event.height) != self._size:
            self.request_backdrop()

    def _on_scene_enter(self, _event: "tkinter.Event[tkinter.Misc]") -> None:
        self._hovering = True
        self._draw_tooltip()

    def _on_scene_leave(self, _event: "tkinter.Event[tkinter.Misc]") -> None:
        self._hovering = False
        self.delete("tooltip")

    def _keep_clear(self) -> list[Rect]:
        rects = [self._scene_box]
        for tag in self._placed:
            box = self.bbox(tag)
            if box:
                rects.append(box)
        for child in self.winfo_children():
            if child.winfo_ismapped():
                x, y = child.winfo_x(), child.winfo_y()
                rects.append((x, y, x + child.winfo_width(), y + child.winfo_height()))
        return rects

    def _render_backdrop(self, width: int, height: int) -> None:
        backdrop = render_backdrop(
            (width, height),
            self.pixel_size,
            self._keep_clear(),
            self._daylight,
            self._palette.background,
        )
        self._backdrop = backdrop
        self._scene_base = None
        self._size = (width, height)
        photo = self._backdrop_photo
        if photo is not None and (photo.width(), photo.height()) == (width, height):
            photo.paste(backdrop.image)
        else:
            photo = self._backdrop_photo = ImageTk.PhotoImage(backdrop.image)
            self.delete("backdrop")
            self.create_image(0, 0, anchor="nw", image=photo, tags="backdrop")
        self.tag_lower("backdrop")

        self.delete("twinkler")
        self._dimmed.clear()
        scale = self.pixel_size
        for x, y in backdrop.twinklers:
            self.create_rectangle(
                x * scale,
                y * scale,
                (x + 1) * scale,
                (y + 1) * scale,
                fill=STAR_BRIGHT,
                width=0,
                tags="twinkler",
            )
        if backdrop.twinklers:
            self.tag_raise("twinkler", "backdrop")

    def _twinkle(self) -> None:
        stars = self.find_withtag("twinkler")
        for star in self._rng.sample(stars, min(TWINKLES_PER_STEP, len(stars))):
            if star in self._dimmed:
                self._dimmed.remove(star)
                self.itemconfigure(star, fill=STAR_BRIGHT)
            else:
                self._dimmed.add(star)
                self.itemconfigure(star, fill=STAR_DIM)

    def _draw_scene(self) -> None:
        left, top, right, bottom = self._scene_box
        size = (right - left, bottom - top)
        art = self._art.frame(self._state, self._tick, self._daylight, self._animate)
        if self._scene_base is None:
            # The backdrop under the dome, kept until the backdrop, theme or layout changes.
            base = Image.new("RGB", size, self._palette.background)
            if self._backdrop is not None:
                base.paste(self._backdrop.image, (-left, -top))
            self._scene_base = base.convert("RGBA")
        # Left as RGBA: it is opaque, and Tk takes it as quickly as RGB.
        scene = self._scene_base.copy()
        scene.alpha_composite(art.resize(size, Image.Resampling.NEAREST))
        if self._scene_photo is None:
            self._scene_photo = ImageTk.PhotoImage(scene)
            self.create_image(left, top, anchor="nw", image=self._scene_photo, tags="scene")
            # Placed images such as the power ring overlap the scene's corners.
            for tag in self._placed:
                self.tag_raise(tag)
        else:
            self._scene_photo.paste(scene)

    def _font(self) -> tuple[str, int]:
        # Negative sizes are pixels, matching how customtkinter scales its own fonts.
        return (FONT_FAMILY, -round(SIZE_CAPTION * self._k))

    def _label_pad(self) -> tuple[int, int]:
        pad_x, pad_y = TOOLTIP_PAD
        return round(pad_x * self._k), round(pad_y * self._k)

    def _draw_tooltip(self) -> None:
        self.delete("tooltip")
        if not self._tooltip:
            return
        _, pad_y = self._label_pad()
        bottom = self._scene_box[1] - round(TOOLTIP_GAP * self._k) - pad_y
        self._draw_label("tooltip", self._tooltip, self.scene_column_px // 2, bottom, "s")

    def _draw_label(self, tag: str, text: str, x: int, y: int, anchor: Literal["ne", "s"]) -> None:
        """Text in a small surface-coloured box, kept on screen and drawn above everything."""
        self.delete(tag)
        pad_x, pad_y = self._label_pad()
        item = self.create_text(
            x,
            y,
            text=text,
            anchor=anchor,
            fill=self._palette.text,
            font=self._font(),
            tags=tag,
        )
        left, top, right, bottom = self.bbox(item)
        shift = max(0, TOOLTIP_MARGIN + pad_x - left)
        self.move(item, shift, 0)
        box = self.create_rectangle(
            left + shift - pad_x,
            top - pad_y,
            right + shift + pad_x,
            bottom + pad_y,
            fill=self._palette.surface,
            outline=self._palette.line,
            tags=tag,
        )
        self.tag_lower(box, item)
        self.tag_raise(tag)
