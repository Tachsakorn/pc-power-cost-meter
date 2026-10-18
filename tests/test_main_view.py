import tkinter
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import customtkinter as ctk
import pytest
from PIL import Image

from power_meter.ui import main_view
from power_meter.ui.main_view import DetailRow, MainView, MainViewModel
from power_meter.ui.scene.art import SceneArt
from power_meter.ui.stage import Stage
from power_meter.ui.theme import DARK, LIGHT

BASE = MainViewModel(
    status="Measuring",
    watts="56",
    load="Light use",
    gauge_fraction=0.2,
    peak="276 W",
    spent="0.0134",
    spent_caption="in 3 min",
    per_hour="฿0.25",
    monthly="≈ ฿39.63",
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


def make_view(stage: Stage, hours: list[int] | None = None) -> MainView:
    view = MainView(
        stage,
        DARK,
        on_open_settings=lambda: None,
        on_reset=lambda: None,
        on_toggle_theme=lambda: None,
        on_hours_change=(hours if hours is not None else []).append,
    )
    view.render(BASE)
    stage.update()
    return view


def mapped_children(stage: Stage) -> list[Any]:
    return [child for child in stage.winfo_children() if child.winfo_ismapped()]


def test_gauge_is_drawn_again_only_when_the_lit_tick_changes(
    stage: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    fractions: list[float] = []
    original = main_view.render_gauge

    def counting(fraction: float, *args: Any) -> Image.Image:
        fractions.append(fraction)
        return original(fraction, *args)

    monkeypatch.setattr(main_view, "render_gauge", counting)
    view = make_view(stage)
    drawn = len(fractions)

    view.render(replace(BASE, gauge_fraction=0.201))
    assert len(fractions) == drawn

    view.render(replace(BASE, gauge_fraction=0.5))
    assert fractions[-1] == 0.5
    assert len(fractions) == drawn + 1


def test_gauge_sits_centred_in_the_dashboard_column(stage: Stage) -> None:
    make_view(stage)

    left, top, right, _ = stage.bbox("gauge")
    column_x, row_y, column_w, _ = stage.grid_bbox(1, main_view.GAUGE_ROW)
    pad_left, pad_right = main_view.PAD

    assert right - left == main_view.GAUGE_W
    assert top == row_y
    assert left - (column_x + pad_left) == (column_x + column_w - pad_right) - right


def test_hide_clears_the_column_and_show_restores_it(stage: Stage) -> None:
    view = make_view(stage)
    shown = len(mapped_children(stage))

    view.hide()
    stage.update()
    assert mapped_children(stage) == []
    assert stage.find_withtag("gauge") == ()

    view.show()
    stage.update()
    assert len(mapped_children(stage)) == shown
    assert stage.find_withtag("gauge") != ()


def test_theme_button_offers_the_other_theme(stage: Stage) -> None:
    view = make_view(stage)
    assert view._theme_button.tooltip == "Switch to light mode"

    view.apply_palette(LIGHT)

    assert view._theme_button.tooltip == "Switch to dark mode"


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


def test_detail_rows_are_rebuilt_when_the_parts_change(stage: Stage) -> None:
    view = make_view(stage)

    view.render(replace(BASE, details=(DetailRow("Battery", "30 W", "measured", True, 1.0),)))

    assert [cells.name.cget("text") for cells in view._detail_cells] == ["Battery"]
