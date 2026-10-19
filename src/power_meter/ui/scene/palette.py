from collections.abc import Sequence
from typing import Final

from PIL import Image

from power_meter.ui.scene.state import SceneState

Rgb = tuple[int, int, int]
Overrides = dict[str, Rgb | str]

ROLES: Final = (
    "clear", "air", "air2", "wood1", "wood2", "wood3", "brass0", "brass1", "brass2",
    "soil0", "soil1", "soil2", "stone1", "stone2", "bark0", "bark1", "bark2",
    "leaf0", "leaf1", "leaf2", "leaf3", "leaf4", "moss", "cap", "glass0", "glass1",
    "hi", "hi2", "core0", "core1", "core2", "core3", "bloom", "water0", "water1", "petal",
)  # fmt: skip
INDEX: Final = {name: i for i, name in enumerate(ROLES)}
HAZE: Final = len(ROLES)
GLOW: Final = HAZE + 1
GLOW_SOIL: Final = HAZE + 2
GLOW_CORE: Final = HAZE + 3

_NIGHT_HAZE: Final[Rgb] = (40, 34, 29)
_DAY_HAZE: Final[Rgb] = (246, 230, 200)

_DAY: Final[dict[str, Rgb]] = {
    "air": (244, 238, 228),
    "air2": (233, 225, 211),
    "glass0": (160, 144, 126),
    "glass1": (132, 116, 100),
    "hi": (255, 253, 248),
    "hi2": (214, 198, 176),
}


def _leaves(*drawn_as: str) -> Overrides:
    return {f"leaf{i}": name for i, name in enumerate(drawn_as)}


_STATES: Final[dict[SceneState, Overrides]] = {
    SceneState.DORMANT: {
        "core0": (70, 72, 74),
        "core1": (104, 106, 108),
        "core2": (136, 138, 140),
        "core3": (136, 138, 140),
        "bloom": (104, 106, 108),
        **_leaves("leaf0", "leaf0", "leaf1", "leaf1", "leaf2"),
    },
    SceneState.IDLE: {
        "bloom": "core1",
        **_leaves("leaf0", "leaf1", "leaf2", "leaf2", "leaf3"),
    },
    SceneState.ACTIVE: {"bloom": "core2"},
    SceneState.HIGH: {
        "core0": "core1",
        "core1": "core2",
        "core2": "core3",
        "bloom": "core3",
        **_leaves("leaf1", "leaf2", "leaf3", "leaf4", "leaf4"),
    },
}

_LIVING: Final = (
    "soil0", "soil1", "soil2", "stone1", "stone2", "bark0", "bark1", "bark2",
    "leaf0", "leaf1", "leaf2", "leaf3", "leaf4", "moss", "cap", "petal", "water0", "water1",
)  # fmt: skip
_GREENS: Final = ("leaf0", "leaf1", "leaf2", "leaf3", "leaf4", "moss")

_LIGHT: Final = {
    SceneState.DORMANT: 0.55,
    SceneState.IDLE: 0.66,
    SceneState.ACTIVE: 0.90,
    SceneState.HIGH: 1.06,
}
_FADED: Final = frozenset({SceneState.DORMANT, SceneState.IDLE})

_TINTS: Final[dict[SceneState, tuple[tuple[str, str, float], ...]]] = {
    SceneState.ACTIVE: (("soil2", "core1", 0.35), ("glass1", "core2", 0.25)),
    SceneState.HIGH: (
        ("soil2", "core2", 0.70),
        ("bark2", "core2", 0.35),
        ("bark1", "core1", 0.20),
        ("leaf4", "core3", 0.45),
        ("leaf3", "core2", 0.20),
        ("glass1", "core2", 0.70),
        ("glass0", "core1", 0.50),
    ),
}


def mix(colour: Sequence[int], target: Sequence[int], amount: float) -> Rgb:
    r, g, b = (round(c + (t - c) * amount) for c, t in zip(colour, target, strict=True))
    return r, g, b


def read_palette(image: Image.Image) -> list[Rgb]:
    flat = image.getpalette()
    if flat is None:
        raise ValueError("sprite has no palette")
    return [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(ROLES) * 3, 3)]


def build_palette(base: Sequence[Rgb], state: SceneState, daylight: bool) -> list[Rgb]:
    """Palette for a state: the night colours with day and state overrides applied by role,
    the living parts lit for the load, and the glow colours appended after the haze."""
    colours = list(base)
    if daylight:
        for role, rgb in _DAY.items():
            colours[INDEX[role]] = rgb
    for role, value in _STATES[state].items():
        colours[INDEX[role]] = base[INDEX[value]] if isinstance(value, str) else value

    light = _LIGHT[state]
    if daylight:
        light = 1 - (1 - light) * 0.6
    for role in _LIVING:
        r, g, b = (min(255, round(c * light)) for c in colours[INDEX[role]])
        colours[INDEX[role]] = (r, g, b)
    if state in _FADED:
        for role in _GREENS:
            grey = round(sum(colours[INDEX[role]]) / 3)
            colours[INDEX[role]] = mix(colours[INDEX[role]], (grey - 4, grey, grey + 10), 0.3)
    for role, core, amount in _TINTS.get(state, ()):
        colours[INDEX[role]] = mix(colours[INDEX[role]], colours[INDEX[core]], amount)

    air2, soil1, core1, core2 = (
        colours[INDEX[name]] for name in ("air2", "soil1", "core1", "core2")
    )
    return [
        *colours,
        _DAY_HAZE if daylight else _NIGHT_HAZE,
        mix(air2, core1, 0.30 if daylight else 0.45),
        mix(soil1, core1, 0.50),
        mix(air2, core2, 0.50 if daylight else 0.70),
    ]
