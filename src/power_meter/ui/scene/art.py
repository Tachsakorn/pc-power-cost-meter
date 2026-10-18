import json
import math
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Final

from PIL import Image

from power_meter.ui.scene.dither import bayer4
from power_meter.ui.scene.palette import HAZE, INDEX, build_palette, read_palette
from power_meter.ui.scene.state import SceneState

NATIVE: Final = (128, 160)

Point = tuple[int, int]
Path = tuple[Point, ...]

_CANOPY_FRAMES: Final = (0, 1, 0, 2)
_CANOPY_STEP_TICKS: Final = 3
_RING_CENTRE: Final = (64, 115)
_RING_STEP_DEGREES: Final = 10
_AIR: Final = frozenset({INDEX["air"], INDEX["air2"]})
_RING_GROUND: Final = _AIR | {INDEX["soil0"], INDEX["soil1"], INDEX["soil2"]}


@dataclass(frozen=True)
class _Effects:
    particles: int
    rings: int
    haze: float


_EFFECTS: Final = {
    SceneState.DORMANT: _Effects(0, 0, 0.0),
    SceneState.IDLE: _Effects(4, 1, 0.04),
    SceneState.ACTIVE: _Effects(10, 2, 0.08),
    SceneState.HIGH: _Effects(18, 3, 0.14),
}


@cache
def _haze_sites(density: float) -> tuple[Point, ...]:
    width, height = NATIVE
    return tuple((x, y) for y in range(height) for x in range(width) if bayer4(x, y) < density)


def _draw_rings(scene: Image.Image, count: int, tick: int) -> None:
    cx, cy = _RING_CENTRE
    for ring in range(1, count + 1):
        colour = INDEX["core1"] if ring == 1 else INDEX["core0"]
        threshold = 0.22 + 0.1 * (3 - ring)
        for degrees in range(0, 360, _RING_STEP_DEGREES):
            angle = math.radians(degrees)
            x = cx + round((7 + 3 * ring) * math.cos(angle))
            y = cy + round((6 + 2 * ring) * math.sin(angle))
            if bayer4(x + tick // 2, y) < threshold and scene.getpixel((x, y)) in _RING_GROUND:
                scene.putpixel((x, y), colour)


def _draw_haze(scene: Image.Image, density: float) -> None:
    if density <= 0:
        return
    for site in _haze_sites(density):
        if scene.getpixel(site) == INDEX["air"]:
            scene.putpixel(site, HAZE)


def _draw_particles(scene: Image.Image, count: int, tick: int, paths: tuple[Path, ...]) -> None:
    for i in range(count):
        path = paths[i % len(paths)]
        colour = INDEX["core2"] if i % 3 == 2 else INDEX["core3"]
        scene.putpixel(path[(i * 7 + 3 + tick) % len(path)], colour)


def _coverage(layer: Image.Image) -> Image.Image:
    return layer.convert("RGBA").getchannel("A")


class SceneArt:
    """Draws the dome scene for a state from the indexed sprite layers."""

    def __init__(
        self,
        back: Image.Image,
        canopy: Image.Image,
        glass: Image.Image,
        paths: tuple[Path, ...],
    ) -> None:
        self.paths = paths
        self._base = read_palette(back)
        self._scenes = [self._compose(back, canopy, glass, frame) for frame in range(3)]
        self._palettes: dict[tuple[SceneState, bool], list[int]] = {}

    @classmethod
    def bundled(cls) -> "SceneArt":
        folder = resources.files("power_meter") / "data" / "scene"

        def load(name: str) -> Image.Image:
            image = Image.open(str(folder / name))
            image.load()
            return image

        raw = json.loads((folder / "paths.json").read_text(encoding="utf-8"))
        ordered = [*raw["roots"], raw["trunk"], *raw["branches"]]
        paths = tuple(tuple((x, y) for x, y in path) for path in ordered)
        return cls(load("back.png"), load("canopy.png"), load("glass.png"), paths)

    def frame(self, state: SceneState, tick: int, daylight: bool, animate: bool) -> Image.Image:
        """RGBA frame at native size; without `animate` it is the still frame for the state."""
        if not animate:
            tick = 0
        step = (tick // _CANOPY_STEP_TICKS) % len(_CANOPY_FRAMES)
        scene = self._scenes[_CANOPY_FRAMES[step]].copy()
        effects = _EFFECTS[state]
        _draw_rings(scene, effects.rings, tick)
        _draw_haze(scene, effects.haze)
        _draw_particles(scene, effects.particles, tick, self.paths)
        scene.putpalette(self._palette(state, daylight))
        return scene.convert("RGBA")

    def _palette(self, state: SceneState, daylight: bool) -> list[int]:
        key = (state, daylight)
        if key not in self._palettes:
            colours = build_palette(self._base, state, daylight)
            self._palettes[key] = [channel for rgb in colours for channel in rgb]
        return self._palettes[key]

    @staticmethod
    def _compose(
        back: Image.Image, canopy: Image.Image, glass: Image.Image, frame: int
    ) -> Image.Image:
        width, height = NATIVE
        leaves = canopy.crop((frame * width, 0, (frame + 1) * width, height))
        scene = back.copy()
        for layer in (leaves, glass):
            scene.paste(layer, (0, 0), _coverage(layer))
        return scene
