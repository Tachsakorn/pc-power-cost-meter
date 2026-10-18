import sys
import time
import tkinter
from collections.abc import Iterator
from typing import Any

import customtkinter as ctk
import pytest
from PIL import Image, ImageTk

from power_meter.ui.scene.art import NATIVE, SceneArt
from power_meter.ui.scene.motion import windows_animations_enabled
from power_meter.ui.scene.state import SceneState
from power_meter.ui.stage import Stage, pixel_scale
from power_meter.ui.theme import DARK, LIGHT

CAPTION = "Active · following live data"
TOOLTIP = "Working — 120 W, 40% of this PC's maximum"


@pytest.fixture(scope="module")
def art() -> SceneArt:
    return SceneArt.bundled()


@pytest.fixture
def window() -> Iterator[ctk.CTk]:
    try:
        root = ctk.CTk()
    except tkinter.TclError as exc:
        pytest.skip(f"Tk can't open a window here: {exc}")
    root.geometry("760x600")
    yield root
    if root.winfo_exists():
        root.destroy()


@pytest.fixture
def scene_pastes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    sizes: list[tuple[int, int]] = []
    original = ImageTk.PhotoImage.paste

    def paste(photo: ImageTk.PhotoImage, image: Image.Image, *args: Any) -> None:
        sizes.append(image.size)
        original(photo, image, *args)

    monkeypatch.setattr(ImageTk.PhotoImage, "paste", paste)
    return sizes


def pump(window: ctk.CTk, seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        window.update()
        time.sleep(0.01)


def make_stage(window: ctk.CTk, art: SceneArt, animate: bool = True) -> Stage:
    stage = Stage(window, DARK, False, art)
    stage.pack(fill="both", expand=True)
    stage.set_animate(animate)
    stage.show_scene(SceneState.ACTIVE, CAPTION, TOOLTIP)
    stage.start()
    pump(window)
    return stage


def scene_draws(stage: Stage, sizes: list[tuple[int, int]]) -> int:
    scene_size = (NATIVE[0] * stage.pixel_size, NATIVE[1] * stage.pixel_size)
    return sizes.count(scene_size)


def pending_ticks(window: ctk.CTk) -> int:
    jobs = window.tk.splitlist(window.tk.call("after", "info"))
    scripts = [str(window.tk.call("after", "info", job)[0]) for job in jobs]
    return sum("_advance" in script for script in scripts)


def tooltip_texts(stage: Stage) -> list[str]:
    return [
        stage.itemcget(item, "text")
        for item in stage.find_withtag("tooltip")
        if stage.type(item) == "text"
    ]


@pytest.mark.parametrize(
    ("available", "scale"),
    [((300, 320), 2), ((450, 480), 3), ((255, 600), 1), ((100, 100), 1)],
)
def test_pixel_scale_uses_whole_factors(available: tuple[int, int], scale: int) -> None:
    assert pixel_scale(available, NATIVE) == scale


def test_scene_redraws_on_each_tick_when_animated(
    window: ctk.CTk, art: SceneArt, scene_pastes: list[tuple[int, int]]
) -> None:
    stage = make_stage(window, art)
    before = scene_draws(stage, scene_pastes)

    pump(window, 0.5)

    assert scene_draws(stage, scene_pastes) - before >= 3


def test_static_scene_draws_once_per_state(
    window: ctk.CTk, art: SceneArt, scene_pastes: list[tuple[int, int]]
) -> None:
    stage = make_stage(window, art, animate=False)
    before = scene_draws(stage, scene_pastes)

    pump(window, 0.5)
    stage.show_scene(SceneState.ACTIVE, CAPTION, TOOLTIP)
    pump(window, 0.2)
    assert scene_draws(stage, scene_pastes) == before
    assert pending_ticks(window) == 0

    stage.show_scene(SceneState.HIGH, "High load · following live data", TOOLTIP)
    pump(window, 0.2)
    assert scene_draws(stage, scene_pastes) == before + 1


def test_turning_animation_back_on_resumes_ticking(window: ctk.CTk, art: SceneArt) -> None:
    stage = make_stage(window, art, animate=False)

    stage.set_animate(True)

    assert pending_ticks(window) == 1


def test_unmap_pauses_and_map_resumes_once(window: ctk.CTk, art: SceneArt) -> None:
    make_stage(window, art)
    assert pending_ticks(window) == 1

    window.event_generate("<Unmap>")
    assert pending_ticks(window) == 0

    window.event_generate("<Map>")
    window.event_generate("<Map>")
    assert pending_ticks(window) == 1


def test_child_widgets_do_not_pause_the_scene(window: ctk.CTk, art: SceneArt) -> None:
    stage = make_stage(window, art)
    panel = tkinter.Frame(stage, width=200, height=200)
    panel.place(x=400, y=100)
    pump(window, 0.1)

    panel.place_forget()
    pump(window, 0.1)

    assert pending_ticks(window) == 1


def test_destroy_cancels_pending_tick(window: ctk.CTk, art: SceneArt) -> None:
    errors: list[object] = []
    window.report_callback_exception = lambda *exc: errors.append(exc)
    stage = make_stage(window, art)

    stage.destroy()
    assert pending_ticks(window) == 0

    window.event_generate("<Map>")
    pump(window, 0.4)
    assert pending_ticks(window) == 0
    assert errors == []


def test_tooltip_appears_on_hover(window: ctk.CTk, art: SceneArt) -> None:
    stage = make_stage(window, art)
    left, top, right, bottom = stage.bbox("scene")
    x, y = (left + right) // 2, (top + bottom) // 2

    stage.event_generate("<Enter>", x=x, y=y)
    stage.event_generate("<Motion>", x=x, y=y)
    assert tooltip_texts(stage) == [TOOLTIP]

    stage.event_generate("<Leave>", x=x, y=y)
    assert tooltip_texts(stage) == []


def test_twinkling_stars_keep_clear_of_widgets(window: ctk.CTk, art: SceneArt) -> None:
    stage = make_stage(window, art)
    panel = tkinter.Frame(stage, width=420, height=520, bg=DARK.background)
    panel.place(x=320, y=40)
    pump(window, 0.1)

    stage.refresh_backdrop()

    stars = [stage.coords(item) for item in stage.find_withtag("twinkler")]
    assert stars
    for left, top, right, bottom in stars:
        assert right < 320 or left > 740 or bottom < 40 or top > 560


def test_placed_image_comes_back_after_its_item_is_deleted(window: ctk.CTk, art: SceneArt) -> None:
    stage = make_stage(window, art)
    image = Image.new("RGBA", (40, 20))
    stage.place_image("badge", image, 400, 30)
    stage.delete("badge")

    stage.place_image("badge", image, 410, 50)

    assert stage.coords("badge") == [410.0, 50.0]


def test_daylight_theme_has_no_twinkling_stars(window: ctk.CTk, art: SceneArt) -> None:
    stage = make_stage(window, art)

    stage.set_theme(LIGHT, True)

    assert stage.cget("bg") == LIGHT.background
    assert stage.find_withtag("twinkler") == ()


def test_windows_animations_flag_is_true_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    assert windows_animations_enabled() is True
