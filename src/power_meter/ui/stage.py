import random
import tkinter
import tkinter.font
from typing import Final

import customtkinter as ctk
from PIL import Image, ImageTk

from power_meter.ui.scene.art import NATIVE, SceneArt
from power_meter.ui.scene.backdrop import CROSS_ARM, CROSS_CENTRE, Backdrop, Rect, render_backdrop
from power_meter.ui.scene.state import SceneState
from power_meter.ui.theme import FONT_FAMILY, SIZE_CAPTION, Palette

TICK_MS: Final = 125
TWINKLE_EVERY: Final = 8
TWINKLES_PER_STEP: Final = 2
SCENE_HEIGHT: Final = 320
SCENE_TOP_NATIVE: Final = 84
CAPTION_GAP: Final = 12
GLYPH_GAP: Final = 6
TOOLTIP_GAP: Final = 8
TOOLTIP_PAD: Final = (8, 4)
TOOLTIP_MARGIN: Final = 4

STAR_BRIGHT: Final = "#{:02X}{:02X}{:02X}".format(*CROSS_CENTRE)
STAR_DIM: Final = "#{:02X}{:02X}{:02X}".format(*CROSS_ARM)


def pixel_scale(available: tuple[int, int], native: tuple[int, int]) -> int:
    return max(1, min(available[0] // native[0], available[1] // native[1]))


class Stage(tkinter.Canvas):
    """Window-filling canvas: pixel backdrop, the animated dome scene and its caption.

    Views are gridded straight onto the stage, right of the scene column; call
    `request_backdrop` after the layout changes so the decorations keep clear of them.
    """

    def __init__(
        self,
        master: tkinter.Misc,
        palette: Palette,
        daylight: bool,
        art: SceneArt,
        scene_box: tuple[int, int] = (300, 600),
    ) -> None:
        super().__init__(master, highlightthickness=0, bd=0, bg=palette.background)
        self._scene_area = scene_box
        self._k: float = ctk.ScalingTracker.get_widget_scaling(self)
        self._fit_scene()

        self._palette = palette
        self._daylight = daylight
        self._art = art
        self._state = SceneState.DORMANT
        self._caption = ""
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
        self.configure(bg=palette.background)
        self._draw_caption()
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

    def place_image(self, tag: str, image: Image.Image, x: int, y: int) -> None:
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
            self.create_image(x, y, anchor="nw", image=photo, tags=tag)

    def show_scene(self, state: SceneState, caption: str, tooltip: str) -> None:
        changed = state is not self._state
        self._state = state
        self._caption = caption
        self._tooltip = tooltip
        self._draw_caption()
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
        self.scene_column_px = round(self._scene_area[0] * self._k)
        self.configure(width=self.scene_column_px, height=round(self._scene_area[1] * self._k))
        self.grid_columnconfigure(0, minsize=self.scene_column_px)
        self.pixel_size = pixel_scale((self.scene_column_px, round(SCENE_HEIGHT * self._k)), NATIVE)
        width, height = NATIVE[0] * self.pixel_size, NATIVE[1] * self.pixel_size
        left, top = (self.scene_column_px - width) // 2, SCENE_TOP_NATIVE * self.pixel_size
        self._scene_box: Rect = (left, top, left + width, top + height)

    def _rescale(self, widget_scaling: float, _window_scaling: float) -> None:
        if widget_scaling == self._k:
            return
        self._k = widget_scaling
        self._fit_scene()
        self.delete("scene", "tooltip")
        self._scene_photo = None
        self._hovering = False
        self._draw_caption()
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
        caption_top = self._caption_top()
        line = tkinter.font.Font(root=self, font=self._font()).metrics("linespace")
        rects = [self._scene_box, (0, caption_top, self.scene_column_px, caption_top + line)]
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
        scene = Image.new("RGB", size, self._palette.background)
        if self._backdrop is not None:
            scene.paste(self._backdrop.image, (-left, -top))
        scene = scene.convert("RGBA")
        scene.alpha_composite(art.resize(size, Image.Resampling.NEAREST))
        scene = scene.convert("RGB")
        if self._scene_photo is None:
            self._scene_photo = ImageTk.PhotoImage(scene)
            self.create_image(left, top, anchor="nw", image=self._scene_photo, tags="scene")
        else:
            self._scene_photo.paste(scene)

    def _font(self) -> tuple[str, int]:
        # Negative sizes are pixels, matching how customtkinter scales its own fonts.
        return (FONT_FAMILY, -round(SIZE_CAPTION * self._k))

    def _caption_top(self) -> int:
        return self._scene_box[3] + round(CAPTION_GAP * self._k)

    def _draw_caption(self) -> None:
        self.delete("caption")
        if not self._caption:
            return
        y = self._caption_top()
        glyph = "○" if self._state is SceneState.DORMANT else "●"
        mark = self.create_text(
            0,
            y,
            text=glyph,
            anchor="nw",
            fill=self._palette.accent,
            font=self._font(),
            tags="caption",
        )
        self.create_text(
            self.bbox(mark)[2] + round(GLYPH_GAP * self._k),
            y,
            text=self._caption,
            anchor="nw",
            fill=self._palette.text_muted,
            font=self._font(),
            tags="caption",
        )
        left, _, right, _ = self.bbox("caption")
        self.move("caption", (self.scene_column_px - (right - left)) // 2 - left, 0)

    def _draw_tooltip(self) -> None:
        self.delete("tooltip")
        if not self._tooltip:
            return
        pad_x, pad_y = (round(pad * self._k) for pad in TOOLTIP_PAD)
        bottom = self._scene_box[1] - round(TOOLTIP_GAP * self._k) - pad_y
        text = self.create_text(
            self.scene_column_px // 2,
            bottom,
            text=self._tooltip,
            anchor="s",
            fill=self._palette.text,
            font=self._font(),
            tags="tooltip",
        )
        left, top, right, bottom = self.bbox(text)
        shift = max(0, TOOLTIP_MARGIN + pad_x - left)
        self.move(text, shift, 0)
        box = self.create_rectangle(
            left + shift - pad_x,
            top - pad_y,
            right + shift + pad_x,
            bottom + pad_y,
            fill=self._palette.surface,
            outline=self._palette.line,
            tags="tooltip",
        )
        self.tag_lower(box, text)
