import math
from typing import Final, NamedTuple

from PIL import Image, ImageChops, ImageColor, ImageDraw

# PIL angles: degrees clockwise from three o'clock. The ring opens at the bottom, under the dome.
START_DEGREES: Final = 140.0
SWEEP_DEGREES: Final = 260.0
STROKE: Final = 6
TICK_GAP: Final = 5
TICK_MAJOR: Final = 8
TICK_MINOR: Final = 4
TICKS: Final = 41
SUPERSAMPLE: Final = 2
# The value layer holds nothing in the ring's opening but the start cap, so the mask can begin
# early enough to keep that cap whole.
MASK_LEAD_DEGREES: Final = 15.0

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


class RingArt:
    """Draws the ring at any fraction from layers made once for its colours and size.

    Each image is a transparent square with the ring at its centre. The value is the full value
    arc cut down to the fraction with a pie-slice mask; the round cap drawn at the cut hides the
    mask's hard edge, so only the layers need anti-aliasing."""

    def __init__(self, colours: RingColours, radius: int, k: float) -> None:
        self.size = 2 * (radius + margin(k))
        self._radius = radius
        self._k = k
        self._base = self._layer(colours, track=True)
        self._value = self._layer(colours, track=False)
        self._cap = self._disc(colours.value)

    def render(self, fraction: float) -> Image.Image:
        fraction = 0.0 if math.isnan(fraction) else min(max(fraction, 0.0), 1.0)
        image = self._base.copy()
        if fraction <= 0:
            return image
        mask = Image.new("L", image.size, 0)
        end = START_DEGREES + SWEEP_DEGREES * fraction
        start = START_DEGREES - MASK_LEAD_DEGREES
        ImageDraw.Draw(mask).pieslice((0, 0, *image.size), start, end, fill=255)
        value = self._value.copy()
        value.putalpha(ImageChops.multiply(value.getchannel("A"), mask))
        image.alpha_composite(value)
        x, y = on_ring(self._radius, fraction)
        half = self.size / 2 - self._cap.width / 2
        image.alpha_composite(self._cap, (round(half + x), round(half + y)))
        return image

    def _layer(self, colours: RingColours, *, track: bool) -> Image.Image:
        """The track with its ticks, or the value arc on its own, both with a cap at the start."""
        scale = self._k * SUPERSAMPLE
        canvas = Image.new("RGBA", (self.size * SUPERSAMPLE,) * 2, (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        centre = self.size * SUPERSAMPLE / 2
        r = self._radius * SUPERSAMPLE
        stroke = STROKE * scale

        def point(at: float, distance: float) -> Point:
            x, y = on_ring(distance, at)
            return centre + x, centre + y

        rgb = ImageColor.getrgb(colours.track if track else colours.value)
        outer = r + stroke / 2
        box = (centre - outer, centre - outer, centre + outer, centre + outer)
        draw.arc(box, START_DEGREES, START_DEGREES + SWEEP_DEGREES, fill=rgb, width=round(stroke))
        for at in (0.0, 1.0) if track else (0.0,):
            x, y = point(at, r)
            draw.ellipse((x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2), fill=rgb)

        if track:
            inner = outer + TICK_GAP * scale
            for i in range(TICKS):
                major = i % 10 == 0
                length = (TICK_MAJOR if major else TICK_MINOR) * scale
                at = i / (TICKS - 1)
                draw.line(
                    (point(at, inner), point(at, inner + length)),
                    fill=ImageColor.getrgb(colours.major_tick if major else colours.tick),
                    width=max(1, round(scale)),
                )
        return canvas.reduce(SUPERSAMPLE)

    def _disc(self, colour: str) -> Image.Image:
        stroke = STROKE * self._k
        side = math.ceil(stroke) + 2
        canvas = Image.new("RGBA", (side * SUPERSAMPLE,) * 2, (0, 0, 0, 0))
        middle, reach = side * SUPERSAMPLE / 2, stroke * SUPERSAMPLE / 2
        box = (middle - reach, middle - reach, middle + reach, middle + reach)
        ImageDraw.Draw(canvas).ellipse(box, fill=ImageColor.getrgb(colour))
        return canvas.reduce(SUPERSAMPLE)
