import pytest
from PIL import Image

from power_meter.ui.gauge import render_gauge

ACCENT = "#E8B866"
TRACK = "#3A312A"
ACCENT_RGB = (0xE8, 0xB8, 0x66)


def count_accent(image: Image.Image) -> int:
    return sum(
        1
        for red, green, blue, alpha in image.get_flattened_data()
        if alpha > 200
        and abs(red - ACCENT_RGB[0]) + abs(green - ACCENT_RGB[1]) < 24
        and abs(blue - ACCENT_RGB[2]) < 24
    )


def test_gauge_is_transparent_rgba_of_the_requested_size() -> None:
    image = render_gauge(0.5, ACCENT, TRACK, 320, 160)
    assert image.size == (320, 160)
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0


def test_gauge_lights_ticks_up_to_the_fraction() -> None:
    low = render_gauge(0.2, ACCENT, TRACK, 320, 160)
    high = render_gauge(0.8, ACCENT, TRACK, 320, 160)
    assert count_accent(high) > count_accent(low) > 0


def test_gauge_scales_with_width() -> None:
    image = render_gauge(0.5, ACCENT, TRACK, 640, 320)
    assert image.size == (640, 320)
    assert count_accent(image) > count_accent(render_gauge(0.5, ACCENT, TRACK, 320, 160))


@pytest.mark.parametrize("fraction", [-1.0, float("nan")])
def test_gauge_clamps_bad_fractions(fraction: float) -> None:
    expected = render_gauge(0.0, ACCENT, TRACK, 320, 160)
    assert render_gauge(fraction, ACCENT, TRACK, 320, 160).tobytes() == expected.tobytes()


def test_gauge_clamps_fractions_above_one() -> None:
    expected = render_gauge(1.0, ACCENT, TRACK, 320, 160)
    assert render_gauge(3.0, ACCENT, TRACK, 320, 160).tobytes() == expected.tobytes()
