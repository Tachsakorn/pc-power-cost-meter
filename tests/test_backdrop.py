import pytest
from PIL import Image

from power_meter.ui.scene.backdrop import Rect, render_backdrop

NIGHT_BG = "#1A1613"
DAY_BG = "#F4EEE4"
SIZE = (760, 600)
KEEP_CLEAR: list[Rect] = [(320, 40, 740, 200), (320, 230, 740, 560)]


def background(hex_colour: str) -> tuple[int, int, int]:
    return (
        int(hex_colour[1:3], 16),
        int(hex_colour[3:5], 16),
        int(hex_colour[5:7], 16),
    )


def test_backdrop_is_exact_size_and_integer_scaled() -> None:
    image = render_backdrop(SIZE, 2, KEEP_CLEAR, False, NIGHT_BG).image
    assert image.size == SIZE
    assert image.mode == "RGB"
    for y in range(0, SIZE[1], 2):
        for x in range(0, SIZE[0], 2):
            block = {image.getpixel((x + dx, y + dy)) for dx in (0, 1) for dy in (0, 1)}
            assert len(block) == 1


def test_odd_sizes_are_cropped_to_fit() -> None:
    image = render_backdrop((301, 199), 3, [], True, DAY_BG).image
    assert image.size == (301, 199)


@pytest.mark.parametrize(("daylight", "colour"), [(False, NIGHT_BG), (True, DAY_BG)])
def test_nothing_drawn_inside_keep_clear_rects(daylight: bool, colour: str) -> None:
    image = render_backdrop(SIZE, 2, KEEP_CLEAR, daylight, colour).image
    base = background(colour)
    for left, top, right, bottom in KEEP_CLEAR:
        for y in range(top, bottom + 1, 3):
            for x in range(left, right + 1, 3):
                assert image.getpixel((x, y)) == base


def test_night_has_stars() -> None:
    image = render_backdrop(SIZE, 2, [], False, NIGHT_BG).image
    base = background(NIGHT_BG)
    marked = sum(1 for pixel in image.get_flattened_data() if pixel != base)
    assert marked > 100


def test_day_has_a_sun() -> None:
    image = render_backdrop(SIZE, 2, [], True, DAY_BG).image
    assert image.getpixel((32, 28)) in {(252, 214, 140), (238, 186, 106)}


def test_same_seed_same_picture() -> None:
    first = render_backdrop(SIZE, 2, KEEP_CLEAR, False, NIGHT_BG, seed=4)
    again = render_backdrop(SIZE, 2, KEEP_CLEAR, False, NIGHT_BG, seed=4)
    other = render_backdrop(SIZE, 2, KEEP_CLEAR, False, NIGHT_BG, seed=5)
    assert first.image.tobytes() == again.image.tobytes()
    assert first.twinklers == again.twinklers
    assert first.image.tobytes() != other.image.tobytes()


def test_twinklers_are_bright_stars_clear_of_widgets() -> None:
    backdrop = render_backdrop(SIZE, 2, KEEP_CLEAR, False, NIGHT_BG)
    assert backdrop.twinklers
    for x, y in backdrop.twinklers:
        assert backdrop.image.getpixel((x * 2, y * 2)) == (240, 230, 206)
        screen_x, screen_y = x * 2, y * 2
        for left, top, right, bottom in KEEP_CLEAR:
            near_x = left - 9 <= screen_x <= right + 8
            near_y = top - 9 <= screen_y <= bottom + 8
            assert not (near_x and near_y)


def test_day_backdrop_has_no_twinklers() -> None:
    assert render_backdrop(SIZE, 2, KEEP_CLEAR, True, DAY_BG).twinklers == ()


def test_day_clouds_are_drawn() -> None:
    image: Image.Image = render_backdrop(SIZE, 2, [], True, DAY_BG).image
    colours = set(image.get_flattened_data())
    assert (253, 251, 247) in colours
    assert (226, 216, 200) in colours
