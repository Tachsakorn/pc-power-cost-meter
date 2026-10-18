import time
from importlib import resources

import pytest
from PIL import Image

from power_meter.ui.scene.art import NATIVE, SceneArt
from power_meter.ui.scene.dither import bayer4
from power_meter.ui.scene.palette import HAZE, INDEX, ROLES, build_palette, read_palette
from power_meter.ui.scene.state import SceneState


@pytest.fixture(scope="module")
def art() -> SceneArt:
    return SceneArt.bundled()


def sprite(name: str) -> Image.Image:
    return Image.open(str(resources.files("power_meter") / "data" / "scene" / name))


def lit_pixels(image: Image.Image, state: SceneState) -> int:
    lit = {
        build_palette(read_palette(sprite("back.png")), state, False)[INDEX[role]]
        for role in ("core2", "core3")
    }
    data = image.tobytes()
    return sum(1 for i in range(0, len(data), 4) if data[i + 3] and tuple(data[i : i + 3]) in lit)


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


def test_dormant_core_is_grey(art: SceneArt) -> None:
    r, g, b, _ = art.frame(SceneState.DORMANT, 0, False, False).getpixel((63, 113))
    assert abs(r - g) <= 2 and abs(g - b) <= 2


def test_high_has_more_lit_pixels_than_idle(art: SceneArt) -> None:
    idle = lit_pixels(art.frame(SceneState.IDLE, 0, False, False), SceneState.IDLE)
    high = lit_pixels(art.frame(SceneState.HIGH, 0, False, False), SceneState.HIGH)
    assert high > idle


def test_dormant_has_no_particles_or_rings(art: SceneArt) -> None:
    assert art.frame(SceneState.DORMANT, 0, False, True) == art.frame(
        SceneState.DORMANT, 0, False, False
    )


def test_static_frame_ignores_the_tick(art: SceneArt) -> None:
    assert art.frame(SceneState.HIGH, 0, False, False) == art.frame(
        SceneState.HIGH, 37, False, False
    )


def test_animation_moves_particles_and_leaves(art: SceneArt) -> None:
    assert art.frame(SceneState.HIGH, 0, False, True) != art.frame(SceneState.HIGH, 3, False, True)


def test_daylight_changes_the_air(art: SceneArt) -> None:
    night = art.frame(SceneState.IDLE, 0, False, False).getpixel((64, 30))
    day = art.frame(SceneState.IDLE, 0, True, False).getpixel((64, 30))
    assert night != day


def test_role_table_matches_the_sprite_palette() -> None:
    base = read_palette(sprite("back.png"))
    assert len(ROLES) == len(base) == 33
    assert build_palette(base, SceneState.ACTIVE, False)[: len(ROLES)] == base
    assert read_palette(sprite("canopy.png")) == base
    assert read_palette(sprite("glass.png")) == base
    assert base[INDEX["air"]] == (26, 22, 19)
    assert base[INDEX["core3"]] == (224, 252, 240)


def test_state_palettes_recolour_by_role() -> None:
    base = read_palette(sprite("back.png"))
    high = build_palette(base, SceneState.HIGH, False)
    assert high[INDEX["core0"]] == base[INDEX["core1"]]
    assert high[INDEX["leaf4"]] == base[INDEX["leaf4"]]
    dormant = build_palette(base, SceneState.DORMANT, False)
    assert dormant[INDEX["leaf2"]] == base[INDEX["leaf1"]]
    assert dormant[INDEX["core3"]] == (136, 138, 140)


def test_day_palette_lightens_the_air_and_haze() -> None:
    base = read_palette(sprite("back.png"))
    day = build_palette(base, SceneState.IDLE, True)
    assert day[INDEX["air"]] == (244, 238, 228)
    assert day[HAZE] == (246, 230, 200)
    assert build_palette(base, SceneState.IDLE, False)[HAZE] == (40, 34, 29)


def test_paths_stay_on_the_canvas(art: SceneArt) -> None:
    assert art.paths
    for path in art.paths:
        for x, y in path:
            assert 0 <= x < NATIVE[0]
            assert 0 <= y < NATIVE[1]


def test_frame_is_cheap(art: SceneArt) -> None:
    started = time.perf_counter()
    for tick in range(100):
        art.frame(SceneState.HIGH, tick, tick % 2 == 0, True)
    assert time.perf_counter() - started < 1.5
