import math
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Final

from PIL import Image

from power_meter.ui.scene.creatures import Creatures
from power_meter.ui.scene.dither import bayer4
from power_meter.ui.scene.layout import Layout, Point, Route
from power_meter.ui.scene.palette import (
    GLOW,
    GLOW_CORE,
    GLOW_SOIL,
    HAZE,
    INDEX,
    Rgb,
    build_palette,
    read_palette,
)
from power_meter.ui.scene.state import SceneState

NATIVE: Final = (128, 160)

_CANOPY_FRAMES: Final = (0, 1, 0, 2)
_CANOPY_STEP_TICKS: Final = 3
_RING_STEP_DEGREES: Final = 10
_AIR: Final = frozenset({INDEX["air"], INDEX["air2"]})
_SOIL: Final = frozenset({INDEX["soil0"], INDEX["soil1"], INDEX["soil2"]})
_RING_GROUND: Final = _AIR | _SOIL
_SPARK_BLOCKERS: Final = frozenset(
    INDEX[role] for role in ("clear", "glass0", "glass1", "hi", "hi2")
)
_SPARK_SPREAD: Final = 24
_SPARK_RISE: Final = 75


@dataclass(frozen=True)
class _Effects:
    particles: int
    rings: int
    haze: float
    glow: int
    sparks: int


_EFFECTS: Final = {
    SceneState.DORMANT: _Effects(0, 0, 0.0, 0, 0),
    SceneState.IDLE: _Effects(3, 1, 0.04, 0, 0),
    SceneState.ACTIVE: _Effects(10, 2, 0.08, 16, 4),
    SceneState.HIGH: _Effects(26, 3, 0.14, 30, 14),
}


@dataclass(frozen=True)
class _Look:
    palette: list[int]
    colours: list[Rgb]
    canopy: tuple[Image.Image, ...]
    glass: Image.Image


@cache
def _haze_sites(density: float) -> tuple[Point, ...]:
    width, height = NATIVE
    return tuple((x, y) for y in range(height) for x in range(width) if bayer4(x, y) < density)


@cache
def _glow_sites(core: Point, radius: int, shift: int) -> tuple[tuple[Point, bool], ...]:
    """Dithered pixels of the glow around the core, each marked True if near its middle."""
    cx, cy = core
    sites = []
    for y in range(cy - radius, cy + radius // 2):
        for x in range(cx - radius, cx + radius + 1):
            distance = math.hypot((x - cx) / radius, (y - cy) / (0.8 * radius))
            if distance < 1 and bayer4(x, y + shift) < (1 - distance) * 0.6:
                sites.append(((x, y), distance < 0.35))
    return tuple(sites)


def _draw_glow(scene: Image.Image, core: Point, radius: int, tick: int) -> None:
    if not radius:
        return
    for site, middle in _glow_sites(core, radius, (tick // 4) % 2):
        index = scene.getpixel(site)
        if index in _AIR:
            scene.putpixel(site, GLOW_CORE if middle else GLOW)
        elif index in _SOIL:
            scene.putpixel(site, GLOW_SOIL)


def _draw_rings(scene: Image.Image, core: Point, count: int, tick: int) -> None:
    cx, cy = core
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


def _draw_particles(scene: Image.Image, count: int, tick: int, routes: tuple[Route, ...]) -> None:
    for i in range(count):
        route = routes[i % len(routes)]
        colour = INDEX["core2"] if i % 3 == 2 else INDEX["core3"]
        scene.putpixel(route[(i * 7 + 3 + tick) % len(route)], colour)


def _draw_sparks(
    scene: Image.Image, core: Point, band: tuple[int, int], count: int, tick: int
) -> None:
    cx, cy = core
    top, bottom = band
    for i in range(count):
        x = cx - _SPARK_SPREAD + (i * 37) % (2 * _SPARK_SPREAD + 1)
        y = cy - 2 - (tick + i * 23) % _SPARK_RISE
        for row, colour in ((y, INDEX["core3"]), (y + 1, INDEX["core1"])):
            if top <= row <= bottom and scene.getpixel((x, row)) not in _SPARK_BLOCKERS:
                scene.putpixel((x, row), colour)


def _coverage(layer: Image.Image) -> Image.Image:
    return layer.convert("RGBA").getchannel("A")


class SceneArt:
    """Draws the dome scene for a state from the indexed sprite layers."""

    def __init__(
        self,
        back: Image.Image,
        canopy: Image.Image,
        glass: Image.Image,
        depth: Image.Image,
        layout: Layout,
    ) -> None:
        width, height = NATIVE
        self.layout = layout
        self.creatures = Creatures(layout, depth, _coverage(back))
        self._back = back
        self._canopy = [canopy.crop((i * width, 0, (i + 1) * width, height)) for i in range(3)]
        self._glass = glass
        self._base = read_palette(back)
        self._looks: dict[tuple[SceneState, bool], _Look] = {}

    @classmethod
    def bundled(cls) -> "SceneArt":
        folder = resources.files("power_meter") / "data" / "scene"

        def load(name: str) -> Image.Image:
            with (folder / name).open("rb") as handle:
                image = Image.open(handle)
                image.load()
            return image

        layout = Layout.parse((folder / "scene.json").read_text(encoding="utf-8"))
        return cls(
            load("back.png"), load("canopy.png"), load("glass.png"), load("depth.png"), layout
        )

    def frame(self, state: SceneState, tick: int, daylight: bool, animate: bool) -> Image.Image:
        """RGBA frame at native size; without `animate` it is the still frame for the state."""
        if not animate:
            tick = 0
        look = self._look(state, daylight)
        effects = _EFFECTS[state]
        core = self.layout.core
        scene = self._back.copy()
        _draw_glow(scene, core, effects.glow, tick)
        _draw_rings(scene, core, effects.rings, tick)
        _draw_haze(scene, effects.haze)
        _draw_particles(scene, effects.particles, tick, self.layout.routes)
        _draw_sparks(scene, core, self.layout.air_band, effects.sparks, tick)
        scene.putpalette(look.palette)
        image = scene.convert("RGBA")

        self.creatures.draw_rabbit(image, look.colours, state, tick)
        step = (tick // _CANOPY_STEP_TICKS) % len(_CANOPY_FRAMES)
        image.alpha_composite(look.canopy[_CANOPY_FRAMES[step]])
        self.creatures.draw_snail(image, look.colours, state, tick)
        image.alpha_composite(look.glass)
        if not daylight:
            self.creatures.draw_fireflies(image, state, tick)
        return image

    def _look(self, state: SceneState, daylight: bool) -> _Look:
        key = (state, daylight)
        if key not in self._looks:
            colours = build_palette(self._base, state, daylight)
            palette = [channel for rgb in colours for channel in rgb]

            def coloured(layer: Image.Image) -> Image.Image:
                layer = layer.copy()
                layer.putpalette(palette)
                return layer.convert("RGBA")

            canopy = tuple(coloured(leaves) for leaves in self._canopy)
            self._looks[key] = _Look(palette, colours, canopy, coloured(self._glass))
        return self._looks[key]
