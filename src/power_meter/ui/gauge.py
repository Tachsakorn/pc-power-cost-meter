import math
from typing import Final, NamedTuple

from PIL import Image, ImageColor, ImageDraw

# PIL angles: degrees clockwise from three o'clock. The ring opens at the bottom, under the dome.
START_DEGREES: Final = 140.0
SWEEP_DEGREES: Final = 260.0
STROKE: Final = 6
TICK_GAP: Final = 5
TICK_MAJOR: Final = 8
TICK_MINOR: Final = 4
TICKS: Final = 41
SUPERSAMPLE: Final = 2

type Point = tuple[float, float]


class RingColours(NamedTuple):
    value: str
    track: str
    tick: str
    major_tick: str


def margin(k: float) -> int:
    """How far the ring's drawing reaches beyond its radius, at UI scale `k`."""
    return math.ceil((STROKE / 2 + TICK_GAP + TICK_MAJOR + 1) * k)


def on_ring(radius: float, fraction: float) -> Point:
    """The point `fraction` of the way along the ring, relative to its centre."""
    angle = math.radians(START_DEGREES + SWEEP_DEGREES * fraction)
    return radius * math.cos(angle), radius * math.sin(angle)


def render_ring(fraction: float, colours: RingColours, radius: int, k: float) -> Image.Image:
    """A square, transparent image with the ring at its centre, lit up to `fraction`."""
    fraction = 0.0 if math.isnan(fraction) else min(max(fraction, 0.0), 1.0)
    half = radius + margin(k)
    canvas = Image.new("RGBA", (2 * half * SUPERSAMPLE,) * 2, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    centre = half * SUPERSAMPLE
    r = radius * SUPERSAMPLE
    stroke = STROKE * k * SUPERSAMPLE

    def point(at: float, distance: float) -> Point:
        x, y = on_ring(distance, at)
        return centre + x, centre + y

    def arc(upto: float, colour: str) -> None:
        rgb = ImageColor.getrgb(colour)
        outer = r + stroke / 2
        box = (centre - outer, centre - outer, centre + outer, centre + outer)
        end = START_DEGREES + SWEEP_DEGREES * upto
        draw.arc(box, START_DEGREES, end, fill=rgb, width=round(stroke))
        for x, y in (point(0.0, r), point(upto, r)):
            draw.ellipse((x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2), fill=rgb)

    arc(1.0, colours.track)
    if fraction > 0:
        arc(fraction, colours.value)

    inner = r + stroke / 2 + TICK_GAP * k * SUPERSAMPLE
    for i in range(TICKS):
        major = i % 10 == 0
        length = (TICK_MAJOR if major else TICK_MINOR) * k * SUPERSAMPLE
        at = i / (TICKS - 1)
        draw.line(
            (point(at, inner), point(at, inner + length)),
            fill=ImageColor.getrgb(colours.major_tick if major else colours.tick),
            width=max(1, round(k * SUPERSAMPLE)),
        )
    return canvas.reduce(SUPERSAMPLE)
