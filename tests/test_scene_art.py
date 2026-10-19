import math
import time
from importlib import resources

import pytest
from PIL import Image, ImageChops

from power_meter.ui.scene.art import NATIVE, SceneArt
from power_meter.ui.scene.creatures import RABBIT, RABBIT_SLEEP, SPRITE_ROLES
from power_meter.ui.scene.dither import bayer4
from power_meter.ui.scene.palette import (
    GLOW_SOIL,
    HAZE,
    INDEX,
    ROLES,
    Rgb,
    build_palette,
    read_palette,
)
from power_meter.ui.scene.state import SceneState

FIREFLY = (236, 240, 140)


@pytest.fixture(scope="module")
def art() -> SceneArt:
    return SceneArt.bundled()


def sprite(name: str) -> Image.Image:
    with (resources.files("power_meter") / "data" / "scene" / name).open("rb") as handle:
        image = Image.open(handle)
        image.load()
    return image


def colours(state: SceneState, daylight: bool = False) -> list[Rgb]:
    return build_palette(read_palette(sprite("back.png")), state, daylight)


def opaque_pixels(image: Image.Image) -> list[tuple[int, int, int]]:
    data = image.tobytes()
    return [(data[i], data[i + 1], data[i + 2]) for i in range(0, len(data), 4) if data[i + 3]]


def colours_in(image: Image.Image) -> set[tuple[int, int, int]]:
    return set(opaque_pixels(image))


def lit_pixels(image: Image.Image, state: SceneState) -> int:
    lit = {colours(state)[INDEX[role]] for role in ("core2", "core3")}
    return sum(1 for rgb in opaque_pixels(image) if rgb in lit)


def mean_luminance(image: Image.Image) -> float:
    pixels = opaque_pixels(image)
    return sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels) / len(pixels)


def sprite_cells(rows: tuple[str, ...], x: int, y: int) -> list[tuple[tuple[int, int], str]]:
    left, top = x - len(rows[0]) // 2, y - len(rows) + 1
    return [
        ((left + c, top + r), letter)
        for r, row in enumerate(rows)
        for c, letter in enumerate(row)
        if letter != "."
    ]


def sprite_matches(
    image: Image.Image, rows: tuple[str, ...], x: int, y: int, palette: list[Rgb]
) -> int:
    return sum(
        1
        for cell, letter in sprite_cells(rows, x, y)
        if image.getpixel(cell)[:3] == palette[INDEX[SPRITE_ROLES[letter]]]
    )


def test_bayer_matrix_is_the_standard_4x4() -> None:
    assert bayer4(0, 0) == 0.0
    assert bayer4(3, 3) == 5 / 16
    assert bayer4(1, 0) == 0.5
    assert bayer4(5, 6) == bayer4(1, 2)


def test_frame_is_native_size_and_clear_outside_the_dome(art: SceneArt) -> None:
    image = art.frame(SceneState.ACTIVE, 0, False, False)
    assert image.mode == "RGBA"
    assert image.size == NATIVE == (128, 160)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((64, 100))[3] == 255


def test_dormant_crystal_is_grey(art: SceneArt) -> None:
    back = sprite("back.png")
    frame = art.frame(SceneState.DORMANT, 0, True, False)
    cores = {INDEX[f"core{i}"] for i in range(4)}
    cx, cy = art.layout.core
    crystal = [
        frame.getpixel((x, y))
        for y in range(cy - 16, cy)
        for x in range(cx - 8, cx + 9)
        if back.getpixel((x, y)) in cores
    ]
    assert len(crystal) > 30
    for r, g, b, _ in crystal:
        assert abs(r - g) <= 2 and abs(g - b) <= 2


def test_high_has_more_lit_pixels_than_idle(art: SceneArt) -> None:
    idle = lit_pixels(art.frame(SceneState.IDLE, 0, False, False), SceneState.IDLE)
    high = lit_pixels(art.frame(SceneState.HIGH, 0, False, False), SceneState.HIGH)
    assert high > idle


def test_states_look_clearly_different(art: SceneArt) -> None:
    idle = mean_luminance(art.frame(SceneState.IDLE, 8, False, True))
    high = mean_luminance(art.frame(SceneState.HIGH, 8, False, True))
    assert high >= 1.25 * idle


def test_states_brighten_with_the_load(art: SceneArt) -> None:
    levels = [
        mean_luminance(art.frame(state, 8, False, True))
        for state in (SceneState.DORMANT, SceneState.IDLE, SceneState.ACTIVE, SceneState.HIGH)
    ]
    assert levels == sorted(levels)


def test_glow_lights_the_ground_only_when_busy(art: SceneArt) -> None:
    def glowing(state: SceneState) -> int:
        target = colours(state)[GLOW_SOIL]
        return sum(1 for rgb in opaque_pixels(art.frame(state, 0, False, False)) if rgb == target)

    assert glowing(SceneState.IDLE) == 0
    assert 0 < glowing(SceneState.ACTIVE) < glowing(SceneState.HIGH)


def test_high_moves_within_a_canopy_step(art: SceneArt) -> None:
    assert art.frame(SceneState.HIGH, 0, False, True) != art.frame(SceneState.HIGH, 2, False, True)


def ring_points(core: tuple[int, int]) -> set[tuple[int, int]]:
    cx, cy = core
    return {
        (
            cx + round((7 + 3 * r) * math.cos(math.radians(a))),
            cy + round((6 + 2 * r) * math.sin(math.radians(a))),
        )
        for r in (1, 2, 3)
        for a in range(0, 360, 10)
    }


def test_rings_shimmer_where_no_particle_travels(art: SceneArt) -> None:
    on_routes = {point for route in art.layout.routes for point in route}
    first = art.frame(SceneState.HIGH, 0, False, True)
    second = art.frame(SceneState.HIGH, 2, False, True)
    assert any(
        first.getpixel(p) != second.getpixel(p) for p in ring_points(art.layout.core) - on_routes
    )


def test_static_frame_ignores_the_tick(art: SceneArt) -> None:
    assert art.frame(SceneState.HIGH, 0, False, False) == art.frame(
        SceneState.HIGH, 37, False, False
    )


def test_animation_moves_particles_and_leaves(art: SceneArt) -> None:
    assert art.frame(SceneState.HIGH, 0, False, True) != art.frame(SceneState.HIGH, 3, False, True)


def test_daylight_changes_the_air(art: SceneArt) -> None:
    night = art.frame(SceneState.IDLE, 0, False, False).getpixel((30, 60))
    day = art.frame(SceneState.IDLE, 0, True, False).getpixel((30, 60))
    assert night != day


def test_idle_rabbit_sleeps_and_busy_rabbit_moves(art: SceneArt) -> None:
    x, y, _ = art.layout.rabbit_bed
    asleep = len(sprite_cells(RABBIT_SLEEP, x, y))
    for tick in (0, 8):
        idle = art.frame(SceneState.IDLE, tick, False, True)
        assert sprite_matches(idle, RABBIT_SLEEP, x, y, colours(SceneState.IDLE)) > asleep // 2
        high = art.frame(SceneState.HIGH, tick, False, True)
        assert sprite_matches(high, RABBIT_SLEEP, x, y, colours(SceneState.HIGH)) < asleep // 2

    xs = [point[0] for point in art.layout.rabbit_loop]
    ys = [point[1] for point in art.layout.rabbit_loop]
    loop_area = (min(xs) - 4, min(ys) - 10, max(xs) + 4, max(ys) + 1)
    first = art.frame(SceneState.HIGH, 0, False, True).crop(loop_area)
    later = art.frame(SceneState.HIGH, 8, False, True).crop(loop_area)
    assert first != later


def test_rabbit_hides_behind_closer_things(art: SceneArt) -> None:
    depth = sprite("depth.png")
    hidden = [
        (x, y, closeness, cell)
        for x, y, closeness in art.layout.rabbit_loop
        for cell, _ in sprite_cells(RABBIT, x, y)
        if depth.getpixel(cell) > closeness + 1
    ]
    assert hidden
    x, y, closeness, cell = hidden[0]
    background = art.frame(SceneState.HIGH, 0, False, False)
    image = background.copy()
    art.creatures.put_sprite(image, RABBIT, colours(SceneState.HIGH), x, y, closeness)
    assert image != background
    assert image.getpixel(cell) == background.getpixel(cell)


def test_fireflies_stay_inside_the_dome(art: SceneArt) -> None:
    outside = sprite("back.png").convert("RGBA").getchannel("A").point(lambda a: 0 if a else 255)
    lit_somewhere = False
    for tick in range(60):
        canvas = Image.new("RGBA", NATIVE, (0, 0, 0, 255))
        art.creatures.draw_fireflies(canvas, SceneState.IDLE, tick)
        changed = canvas.convert("L")
        lit_somewhere |= changed.getbbox() is not None
        assert ImageChops.multiply(changed, outside).getbbox() is None
    assert lit_somewhere


def test_no_fireflies_in_daylight(art: SceneArt) -> None:
    ticks = range(14)
    assert any(FIREFLY in colours_in(art.frame(SceneState.IDLE, t, False, True)) for t in ticks)
    assert all(FIREFLY not in colours_in(art.frame(SceneState.IDLE, t, True, True)) for t in ticks)


def test_role_table_matches_the_sprite_palette() -> None:
    base = read_palette(sprite("back.png"))
    assert len(ROLES) == len(base) == 36
    assert ROLES[-4:] == ("bloom", "water0", "water1", "petal")
    assert read_palette(sprite("canopy.png")) == base
    assert read_palette(sprite("glass.png")) == base
    assert base[INDEX["air"]] == (26, 22, 19)
    assert base[INDEX["core3"]] == (224, 252, 240)
    active = build_palette(base, SceneState.ACTIVE, False)
    for role in ("air", "air2", "wood1", "brass1", "core1", "core2", "hi"):
        assert active[INDEX[role]] == base[INDEX[role]]


def test_state_palettes_recolour_by_role() -> None:
    base = read_palette(sprite("back.png"))
    high = build_palette(base, SceneState.HIGH, False)
    assert high[INDEX["core0"]] == base[INDEX["core1"]]
    assert sum(high[INDEX["leaf4"]]) > sum(base[INDEX["leaf4"]])
    dormant = build_palette(base, SceneState.DORMANT, False)
    assert dormant[INDEX["core3"]] == (136, 138, 140)
    assert sum(dormant[INDEX["leaf2"]]) < sum(base[INDEX["leaf1"]])


def test_quiet_states_fade_the_greens() -> None:
    def spread(rgb: Rgb) -> int:
        return max(rgb) - min(rgb)

    base = read_palette(sprite("back.png"))
    idle = build_palette(base, SceneState.IDLE, False)
    active = build_palette(base, SceneState.ACTIVE, False)
    assert spread(idle[INDEX["moss"]]) < spread(active[INDEX["moss"]]) * 0.66


def test_daylight_softens_the_dimming() -> None:
    base = read_palette(sprite("back.png"))
    night = build_palette(base, SceneState.IDLE, False)[INDEX["stone2"]]
    day = build_palette(base, SceneState.IDLE, True)[INDEX["stone2"]]
    assert sum(night) < sum(day) < sum(base[INDEX["stone2"]])


def test_day_palette_lightens_the_air_and_haze() -> None:
    base = read_palette(sprite("back.png"))
    day = build_palette(base, SceneState.IDLE, True)
    assert day[INDEX["air"]] == (244, 238, 228)
    assert day[HAZE] == (246, 230, 200)
    assert build_palette(base, SceneState.IDLE, False)[HAZE] == (40, 34, 29)
    assert len(day) == len(ROLES) + 4


def test_scene_points_stay_on_the_canvas(art: SceneArt) -> None:
    layout = art.layout
    points = [point for route in layout.routes for point in route]
    points += [*layout.log, layout.core, layout.rabbit_bed[:2]]
    points += [(x, y) for x, y, _ in layout.rabbit_loop]
    assert layout.routes and len(layout.rabbit_loop) == 140
    for x, y in points:
        assert 0 <= x < NATIVE[0]
        assert 0 <= y < NATIVE[1]


def test_frame_is_cheap(art: SceneArt) -> None:
    started = time.perf_counter()
    for tick in range(100):
        art.frame(SceneState.HIGH, tick, tick % 2 == 0, True)
    assert time.perf_counter() - started < 1.5
