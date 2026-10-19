import time
import tkinter
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any

import customtkinter as ctk
import pytest
from PIL import Image

from power_meter.ui import main_view
from power_meter.ui.gauge import RingArt, margin
from power_meter.ui.main_view import (
    ESTIMATES_ONLY,
    RESTARTING,
    WAITING,
    DetailRow,
    MainView,
    MainViewModel,
)
from power_meter.ui.scene.art import SceneArt
from power_meter.ui.stage import Stage
from power_meter.ui.theme import DARK, LIGHT

BASE = MainViewModel(
    status="Live",
    live=True,
    watts="56",
    load="Light use",
    gauge_fraction=0.2,
    peak="276 W",
    spent="0.0134",
    spent_caption="3 min measured",
    per_hour="฿0.25",
    monthly="≈ ฿39.63",
    monthly_ready=True,
    hours_per_day=5,
    details=(
        DetailRow("CPU", "18 W", "measured", True, 0.72),
        DetailRow("Other", "25 W", "estimated", False, 1.0),
    ),
    price="Normal meter · ฿4.50 per unit (default)",
    notice=None,
)


@pytest.fixture
def stage() -> Iterator[Stage]:
    try:
        root = ctk.CTk()
    except tkinter.TclError as exc:
        pytest.skip(f"Tk can't open a window here: {exc}")
    root.geometry("760x600")
    stage = Stage(root, DARK, False, SceneArt.bundled())
    stage.pack(fill="both", expand=True)
    yield stage
    root.destroy()


def make_view(stage: Stage, hours: list[int] | None = None, *, animate: bool = False) -> MainView:
    view = MainView(
        stage,
        DARK,
        on_open_settings=lambda: None,
        on_reset=lambda: None,
        on_toggle_theme=lambda: None,
        on_hours_change=(hours if hours is not None else []).append,
    )
    view.set_animate(animate)
    view.render(BASE)
    stage.update()
    return view


def mapped_children(stage: Stage) -> list[Any]:
    return [child for child in stage.winfo_children() if child.winfo_ismapped()]


def pending_reset_timers(widget: tkinter.Misc) -> int:
    jobs = widget.tk.splitlist(widget.tk.call("after", "info"))
    scripts = [str(widget.tk.call("after", "info", job)[0]) for job in jobs]
    return sum("_end_reset_confirm" in script for script in scripts)


def pump(widget: tkinter.Misc, seconds: float = 0.2) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        widget.update()
        time.sleep(0.01)


@pytest.fixture
def drawn(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    fractions: list[float] = []

    render = RingArt.render

    def recording(art: RingArt, fraction: float) -> Image.Image:
        fractions.append(fraction)
        return render(art, fraction)

    monkeypatch.setattr(RingArt, "render", recording)
    return fractions


def test_ring_is_drawn_again_only_when_its_sweep_changes(stage: Stage, drawn: list[float]) -> None:
    view = make_view(stage)
    count = len(drawn)

    view.render(replace(BASE, gauge_fraction=0.20001))
    assert len(drawn) == count

    view.render(replace(BASE, gauge_fraction=0.5))
    assert drawn[-1] == pytest.approx(0.5)
    assert len(drawn) == count + 1


def test_ring_eases_towards_a_new_reading(stage: Stage, drawn: list[float]) -> None:
    view = make_view(stage, animate=True)

    view.render(replace(BASE, gauge_fraction=0.8))
    assert 0.2 < drawn[-1] < 0.8

    pump(stage, 1.0)
    assert drawn[-1] == pytest.approx(0.8, abs=1e-3)
    steps = drawn[drawn.index(next(f for f in drawn if f > 0.2)) :]
    assert steps == sorted(steps)


def test_small_changes_are_drawn_at_once(stage: Stage, drawn: list[float]) -> None:
    view = make_view(stage, animate=True)

    view.render(replace(BASE, gauge_fraction=0.21))

    assert drawn[-1] == pytest.approx(0.21, abs=1e-3)


def test_turning_animation_off_finishes_the_ease(stage: Stage, drawn: list[float]) -> None:
    view = make_view(stage, animate=True)
    view.render(replace(BASE, gauge_fraction=0.8))

    view.set_animate(False)

    assert drawn[-1] == pytest.approx(0.8, abs=1e-3)
    pump(stage, 0.2)
    assert drawn[-1] == pytest.approx(0.8, abs=1e-3)


def pending_eases(widget: tkinter.Misc) -> int:
    jobs = widget.tk.splitlist(widget.tk.call("after", "info"))
    return sum("_ease" in str(widget.tk.call("after", "info", job)[0]) for job in jobs)


def test_hiding_the_dashboard_stops_the_ease(stage: Stage) -> None:
    view = make_view(stage, animate=True)
    view.render(replace(BASE, gauge_fraction=0.8))
    assert pending_eases(stage) == 1

    view.hide()

    assert pending_eases(stage) == 0


def test_no_easing_while_the_window_is_minimised(stage: Stage, drawn: list[float]) -> None:
    view = make_view(stage, animate=True)
    stage.winfo_toplevel().withdraw()
    stage.update()

    view.render(replace(BASE, gauge_fraction=0.8))

    assert drawn[-1] == pytest.approx(0.8, abs=1e-3)
    assert pending_eases(stage) == 0


def test_ring_is_centred_on_the_stage_ring(stage: Stage) -> None:
    make_view(stage)

    left, top, right, bottom = stage.bbox("ring")
    ring = stage.ring
    assert right - left == 2 * (ring.radius + margin(1.0))
    assert ((left + right) // 2, (top + bottom) // 2) == (ring.x, ring.y)


def test_readout_sits_under_the_dome(stage: Stage) -> None:
    view = make_view(stage)

    readout = view._readout
    middle = readout.winfo_x() + readout.winfo_width() / 2
    assert abs(middle - stage.ring.x) <= 1
    assert readout.winfo_y() > stage.scene_bottom


def test_scale_ends_sit_outside_the_ring(stage: Stage) -> None:
    view = make_view(stage)

    zero, peak = view._zero, view._peak
    ring = stage.ring
    assert zero.winfo_x() + zero.winfo_width() <= ring.x - ring.radius * 0.7
    assert peak.winfo_x() >= ring.x + ring.radius * 0.7
    assert peak.winfo_x() + peak.winfo_width() <= stage.scene_column_px


def test_hide_clears_the_column_and_show_restores_it(stage: Stage) -> None:
    view = make_view(stage)
    shown = len(mapped_children(stage))

    view.hide()
    stage.update()
    assert mapped_children(stage) == []
    assert stage.find_withtag("ring") == ()

    view.show()
    stage.update()
    assert len(mapped_children(stage)) == shown
    assert stage.find_withtag("ring") != ()


def hint_texts(stage: Stage) -> list[str]:
    return [
        stage.itemcget(item, "text")
        for item in stage.find_withtag("hint")
        if stage.type(item) == "text"
    ]


def test_theme_button_offers_the_other_theme(stage: Stage) -> None:
    view = make_view(stage)
    assert view._theme_button.hint == "Light mode"

    view.apply_palette(LIGHT)

    assert view._theme_button.hint == "Dark mode"


def test_icon_button_hint_follows_hover_and_focus(stage: Stage) -> None:
    view = make_view(stage)
    button = view._theme_button

    tkinter.Misc.event_generate(button, "<Enter>")
    assert hint_texts(stage) == ["Light mode"]
    tkinter.Misc.event_generate(button, "<Leave>")
    assert hint_texts(stage) == []

    button.focus_force()
    pump(stage, 0.1)
    assert hint_texts(stage) == ["Light mode"]
    stage.focus_force()
    pump(stage, 0.1)
    assert hint_texts(stage) == []


def test_clicking_an_icon_button_dismisses_its_hint(stage: Stage) -> None:
    view = make_view(stage)
    tkinter.Misc.event_generate(view._settings_button, "<Enter>")
    assert hint_texts(stage) == ["Settings"]

    view._settings_button.invoke()

    assert hint_texts(stage) == []


def test_hint_sits_under_its_button_and_inside_the_window(stage: Stage) -> None:
    view = make_view(stage)
    button = view._settings_button

    tkinter.Misc.event_generate(button, "<Enter>")

    [box] = [item for item in stage.find_withtag("hint") if stage.type(item) == "rectangle"]
    left, top, right, _ = stage.coords(box)
    button_right = button.winfo_rootx() - stage.winfo_rootx() + button.winfo_width()
    button_bottom = button.winfo_rooty() - stage.winfo_rooty() + button.winfo_height()
    assert top > button_bottom
    assert left >= 0
    assert abs(right - button_right) <= 1


def test_hiding_the_dashboard_removes_the_hint(stage: Stage) -> None:
    view = make_view(stage)
    tkinter.Misc.event_generate(view._theme_button, "<Enter>")

    view.hide()

    assert hint_texts(stage) == []


def status_dot(view: MainView) -> tuple[str, str]:
    return view._status_dot.cget("text"), view._status_dot.cget("text_color")


def test_status_dot_is_muted_while_a_notice_is_shown(stage: Stage) -> None:
    view = make_view(stage)
    assert status_dot(view) == ("●", DARK.accent)

    view.render(replace(BASE, status=ESTIMATES_ONLY, notice=ESTIMATES_ONLY))
    assert status_dot(view) == ("●", DARK.text_muted)

    view.render(replace(BASE, status=RESTARTING, notice=RESTARTING, live=False))
    assert status_dot(view) == ("○", DARK.text_muted)

    view.render(BASE)
    assert status_dot(view) == ("●", DARK.accent)


def test_dashboard_follows_a_scaling_change(
    stage: Stage, set_scaling: Callable[[float], None]
) -> None:
    view = make_view(stage)

    set_scaling(1.5)
    pump(stage, 0.3)

    left, top, right, bottom = stage.bbox("ring")
    ring = stage.ring
    assert right - left == 2 * (ring.radius + margin(1.5))
    assert ((left + right) // 2, (top + bottom) // 2) == (ring.x, ring.y)
    assert view._readout.winfo_y() > stage.scene_bottom
    assert {int(cells.track.cget("height")) for cells in view._detail_cells} == {
        round(main_view.BAR_H * 1.5)
    }


def test_hours_stepper_stops_at_its_limits(stage: Stage) -> None:
    hours: list[int] = []
    view = make_view(stage, hours)
    view.render(replace(BASE, hours_per_day=main_view.HOURS_MAX))

    view._hours_up.invoke()
    view._hours_down.invoke()

    assert view._hours_up.cget("state") == "normal"
    assert hours == [main_view.HOURS_MAX - 1]


def test_detail_bars_follow_each_share(stage: Stage) -> None:
    view = make_view(stage)

    widths = [float(cells.fill.place_info()["relwidth"]) for cells in view._detail_cells]

    assert widths == [0.72, 1.0]


def test_breakdown_says_when_there_is_no_reading_yet(stage: Stage) -> None:
    view = make_view(stage)
    assert not view._no_details.winfo_ismapped()

    view.render(replace(BASE, details=()))
    stage.update()
    assert view._no_details.winfo_ismapped()
    assert view._no_details.cget("text") == WAITING

    view.render(BASE)
    stage.update()
    assert not view._no_details.winfo_ismapped()


def test_monthly_is_muted_until_ready(stage: Stage) -> None:
    view = make_view(stage)
    assert view._monthly.cget("text_color") == DARK.text

    view.render(replace(BASE, monthly="ready in 30 s", monthly_ready=False))
    assert view._monthly.cget("text_color") == DARK.text_muted

    view.render(BASE)
    assert view._monthly.cget("text_color") == DARK.text


def test_detail_rows_are_rebuilt_when_the_parts_change(stage: Stage) -> None:
    view = make_view(stage)

    view.render(replace(BASE, details=(DetailRow("Battery", "30 W", "measured", True, 1.0),)))

    assert [cells.name.cget("text") for cells in view._detail_cells] == ["Battery"]


def test_new_detail_rows_refresh_the_backdrop_while_shown(
    stage: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = make_view(stage)
    refreshes: list[None] = []
    monkeypatch.setattr(stage, "request_backdrop", lambda: refreshes.append(None))
    battery = replace(BASE, details=(DetailRow("Battery", "30 W", "measured", True, 1.0),))

    view.render(battery)
    assert len(refreshes) == 1
    view.render(battery)
    assert len(refreshes) == 1

    view.hide()
    view.render(BASE)
    assert len(refreshes) == 1


def test_hide_disarms_reset(stage: Stage) -> None:
    view = make_view(stage)
    view._reset_button.invoke()
    assert view._reset_button.cget("text") == "Click again to reset"

    view.hide()

    assert view._reset_button.cget("text") == "Reset"
    assert pending_reset_timers(stage) == 0


def test_destroying_the_stage_with_reset_armed_leaves_nothing_behind(stage: Stage) -> None:
    errors: list[object] = []
    root: Any = stage.winfo_toplevel()
    root.report_callback_exception = lambda *exc: errors.append(exc)
    view = make_view(stage)
    view._reset_button.invoke()
    assert pending_reset_timers(root) == 1

    stage.destroy()
    pump(root)

    assert pending_reset_timers(root) == 0
    assert errors == []
