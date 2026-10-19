import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from PIL import Image

from power_meter.ui.scene.layout import Layout
from power_meter.ui.scene.palette import INDEX, Rgb, mix
from power_meter.ui.scene.state import SceneState

Sprite = tuple[str, ...]

RABBIT: Final[Sprite] = ("....pw.", "....hh.", ".hhhhkh", "thhhhhh", ".whhww.", ".w..w..")
RABBIT_HOP: Final[Sprite] = ("....pw.", "....hh.", ".hhhhkh", "thhhhhh", ".whhhw.", "w....w.")
RABBIT_SLEEP: Final[Sprite] = ("..hhh..", ".hhhhhw", "thhhhhw", ".wwwww.")
SNAIL: Final[Sprite] = (".k.k..", "..hcb.", ".hbcch", "hhhhhh")
SPRITE_ROLES: Final = {
    "h": "hi2",
    "w": "wood3",
    "k": "bark0",
    "p": "cap",
    "t": "hi",
    "c": "cap",
    "b": "brass1",
}

_SNORE: Final = ((4, -6), (6, -8))
_HOP: Final = (0, 2, 3, 1)
_SNAIL_LIFT: Final = 8

_FIREFLY: Final[Rgb] = (236, 240, 140)
_NEAR: Final = ((1, 0), (-1, 0), (0, 1), (0, -1))
_HALO: Final = ((2, 0), (-2, 0), (0, 2), (0, -2), (1, 1), (-1, -1), (1, -1), (-1, 1))
_EDGE: Final = 2
_BRIGHT: Final = (
    ((0, 0), _FIREFLY, 1.0),
    *(((dx, dy), _FIREFLY, 0.45) for dx, dy in _NEAR),
    *(((dx, dy), _FIREFLY, 0.18) for dx, dy in _HALO),
)
_DIM: Final = (
    ((0, 0), mix(_FIREFLY, (60, 60, 40), 0.3), 1.0),
    *(((dx, dy), _FIREFLY, 0.25) for dx, dy in _NEAR),
)


@dataclass(frozen=True)
class _Habits:
    hop_step: int
    snail_ticks: int
    fireflies: int


_HABITS: Final = {
    SceneState.DORMANT: _Habits(0, 10, 6),
    SceneState.IDLE: _Habits(0, 10, 10),
    SceneState.ACTIVE: _Habits(1, 6, 4),
    SceneState.HIGH: _Habits(2, 4, 8),
}


class Creatures:
    """The rabbit, the snail and the fireflies, drawn onto a coloured frame.

    `depth` is the depth map of the back layer and `dome` its coverage, both "L" images.
    """

    def __init__(self, layout: Layout, depth: Image.Image, dome: Image.Image) -> None:
        self._layout = layout
        self._width = depth.width
        self._depth = depth.tobytes()
        self._dome = dome.tobytes()

    def draw_rabbit(
        self, image: Image.Image, colours: Sequence[Rgb], state: SceneState, tick: int
    ) -> None:
        step = _HABITS[state].hop_step
        if not step:
            x, y, closeness = self._layout.rabbit_bed
            self.put_sprite(image, RABBIT_SLEEP, colours, x, y, closeness)
            if (tick // 6) % 2 == 0:
                for dx, dy in _SNORE:
                    image.putpixel((x + dx, y + dy), colours[INDEX["hi2"]])
            return
        loop = self._layout.rabbit_loop
        index = tick * step % len(loop)
        x, y, closeness = loop[index]
        hop = _HOP[tick % len(_HOP)]
        heading_left = loop[(index + 1) % len(loop)][0] < x
        rows = RABBIT_HOP if hop else RABBIT
        self.put_sprite(image, rows, colours, x, y - hop, closeness, flip=heading_left)

    def draw_snail(
        self, image: Image.Image, colours: Sequence[Rgb], state: SceneState, tick: int
    ) -> None:
        log = self._layout.log
        x, y = log[tick // _HABITS[state].snail_ticks % len(log)]
        self.put_sprite(image, SNAIL, colours, x, y - _SNAIL_LIFT)

    def draw_fireflies(self, image: Image.Image, state: SceneState, tick: int) -> None:
        width, height = image.size
        pixels = bytearray(image.tobytes())
        for i in range(_HABITS[state].fireflies):
            phase = (tick + i * 5) % 14
            if phase >= 12:
                continue
            x = math.floor(
                64 + 36 * math.sin(tick * 0.05 + i * 1.7) + 6 * math.sin(tick * 0.21 + i)
            )
            y = math.floor(76 + 22 * math.cos(tick * 0.07 + i * 2.3) - (i % 5) * 7)
            if not (_EDGE <= x < width - _EDGE and _EDGE <= y < height - _EDGE):
                continue
            if not (self._in_air(x, y) and self._in_air(x, y - 3)):
                continue
            for (dx, dy), colour, amount in _BRIGHT if phase < 7 else _DIM:
                self._blend(pixels, x + dx, y + dy, colour, amount)
        image.frombytes(pixels)

    def put_sprite(
        self,
        image: Image.Image,
        rows: Sprite,
        colours: Sequence[Rgb],
        x: int,
        y: int,
        closeness: int | None = None,
        flip: bool = False,
    ) -> None:
        """Draws `rows` centred on x with the bottom row on y. With a closeness, pixels that
        nearer parts of the scene would cover are left out."""
        width, height = image.size
        left, top = x - len(rows[0]) // 2, y - len(rows) + 1
        for r, row in enumerate(rows):
            for c, letter in enumerate(row[::-1] if flip else row):
                px, py = left + c, top + r
                if letter == "." or not (0 <= px < width and 0 <= py < height):
                    continue
                if closeness is not None and not self._visible(px, py, closeness):
                    continue
                image.putpixel((px, py), colours[INDEX[SPRITE_ROLES[letter]]])

    def _visible(self, x: int, y: int, closeness: int) -> bool:
        behind = self._depth[y * self._width + x]
        return behind == 0 or closeness >= behind - 1

    def _in_air(self, x: int, y: int) -> bool:
        top, bottom = self._layout.air_band
        return top <= y <= bottom and bool(self._dome[y * self._width + x])

    def _blend(self, pixels: bytearray, x: int, y: int, colour: Rgb, amount: float) -> None:
        site = y * self._width + x
        if not self._dome[site]:
            return
        start = site * 4
        pixels[start : start + 3] = bytes(mix(pixels[start : start + 3], colour, amount))
