import math

import pytest
from PIL import Image

from power_meter.ui.gauge import RingColours, margin, on_ring, render_ring

COLOURS = RingColours(value="#E8B866", track="#3A312A", tick="#3A312A", major_tick="#6E635A")
VALUE_RGB = (0xE8, 0xB8, 0x66)
RADIUS = 144


def is_value(pixel: tuple[int, ...]) -> bool:
    red, green, blue, alpha = pixel
    return alpha > 200 and all(
        abs(a - b) < 24 for a, b in zip((red, green, blue), VALUE_RGB, strict=True)
    )


def count_value(image: Image.Image) -> int:
    return sum(1 for pixel in image.get_flattened_data() if is_value(pixel))


def value_at(image: Image.Image, fraction: float) -> bool:
    centre = image.width / 2
    x, y = on_ring(RADIUS, fraction)
    return is_value(image.getpixel((round(centre + x), round(centre + y))))


def test_ring_is_a_transparent_square_centred_on_the_ring() -> None:
    image = render_ring(0.5, COLOURS, RADIUS, 1.0)
    assert image.size == (2 * (RADIUS + margin(1.0)),) * 2
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((image.width // 2, image.height // 2))[3] == 0


def test_value_fills_the_ring_from_its_start_up_to_the_fraction() -> None:
    image = render_ring(0.4, COLOURS, RADIUS, 1.0)

    assert value_at(image, 0.05)
    assert value_at(image, 0.35)
    assert not value_at(image, 0.45)
    assert not value_at(image, 0.9)


def test_more_power_lights_more_of_the_ring() -> None:
    low = render_ring(0.2, COLOURS, RADIUS, 1.0)
    high = render_ring(0.8, COLOURS, RADIUS, 1.0)
    assert count_value(high) > count_value(low) > 0


def test_nothing_is_lit_at_zero() -> None:
    assert count_value(render_ring(0.0, COLOURS, RADIUS, 1.0)) == 0


def test_ring_opens_at_the_bottom() -> None:
    start, end = on_ring(1.0, 0.0), on_ring(1.0, 1.0)
    assert start[1] > 0 and end[1] > 0
    assert start[0] == pytest.approx(-end[0])
    assert on_ring(1.0, 0.5) == pytest.approx((0.0, -1.0))


def test_strokes_scale_with_the_ui() -> None:
    image = render_ring(0.5, COLOURS, RADIUS, 2.0)
    assert image.size == (2 * (RADIUS + margin(2.0)),) * 2
    assert count_value(image) > count_value(render_ring(0.5, COLOURS, RADIUS, 1.0))


@pytest.mark.parametrize("fraction", [-1.0, math.nan])
def test_ring_clamps_bad_fractions(fraction: float) -> None:
    expected = render_ring(0.0, COLOURS, RADIUS, 1.0)
    assert render_ring(fraction, COLOURS, RADIUS, 1.0).tobytes() == expected.tobytes()


def test_ring_clamps_fractions_above_one() -> None:
    expected = render_ring(1.0, COLOURS, RADIUS, 1.0)
    assert render_ring(3.0, COLOURS, RADIUS, 1.0).tobytes() == expected.tobytes()
