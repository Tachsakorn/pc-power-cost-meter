import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PIL import Image

from power_meter.ui.scene.dither import bayer4

type Rect = tuple[int, int, int, int]
type Rgb = tuple[int, int, int]
type Blocked = Callable[[int, int, int], bool]

REFERENCE_AREA = 380 * 281
CLEARANCE = 3
TWINKLER_CLEARANCE = 8

FAINT_STARS: tuple[Rgb, ...] = ((96, 86, 74), (122, 110, 94), (150, 138, 118))
COLOURED_STARS: tuple[Rgb, ...] = ((214, 200, 172), (126, 178, 176))
CROSS_CENTRE: Rgb = (240, 230, 206)
CROSS_ARM: Rgb = (150, 138, 118)
CROSS_ARMS = ((1, 0), (-1, 0), (0, 1), (0, -1))

SUN_CENTRE = (16, 14)
SUN_CORE: Rgb = (252, 214, 140)
SUN_RIM: Rgb = (238, 186, 106)
SUN_RAY: Rgb = (240, 196, 120)
SUN_HALO: Rgb = (250, 232, 196)
LIGHT_BAND: Rgb = (249, 244, 234)
CLOUD_TOP: Rgb = (253, 251, 247)
CLOUD_UNDERSIDE: Rgb = (226, 216, 200)
SEED: Rgb = (206, 186, 150)
BIRD: Rgb = (150, 132, 108)
BIRD_SHAPE = ((-2, -1), (-1, 0), (0, 0), (1, 0), (2, -1))


@dataclass(frozen=True)
class Backdrop:
    image: Image.Image
    twinklers: tuple[tuple[int, int], ...]


def render_backdrop(
    size: tuple[int, int],
    scale: int,
    keep_clear: Sequence[Rect],
    daylight: bool,
    background: str,
    seed: int = 11,
) -> Backdrop:
    width, height = size
    native = (math.ceil(width / scale), math.ceil(height / scale))
    base = _parse_hex(background)
    image = Image.new("RGB", native, base)
    rng = random.Random(seed)
    blocked = _blocked_test(keep_clear, scale)
    area_ratio = native[0] * native[1] / REFERENCE_AREA
    twinklers: tuple[tuple[int, int], ...] = ()
    if daylight:
        _paint_day(image, base, rng, blocked, area_ratio)
    else:
        twinklers = _paint_night(image, base, rng, blocked, area_ratio)
    scaled = image.resize((native[0] * scale, native[1] * scale), Image.Resampling.NEAREST)
    return Backdrop(scaled.crop((0, 0, width, height)), twinklers)


def _parse_hex(colour: str) -> Rgb:
    value = int(colour.lstrip("#"), 16)
    return (value >> 16) & 255, (value >> 8) & 255, value & 255


def _blocked_test(keep_clear: Sequence[Rect], scale: int) -> Blocked:
    def blocked(x: int, y: int, margin: int) -> bool:
        screen_left, screen_top = x * scale, y * scale
        screen_right, screen_bottom = screen_left + scale - 1, screen_top + scale - 1
        return any(
            screen_left <= right + margin
            and screen_right >= left - margin
            and screen_top <= bottom + margin
            and screen_bottom >= top - margin
            for left, top, right, bottom in keep_clear
        )

    return blocked


def _scaled(count: int, area_ratio: float) -> int:
    return round(count * area_ratio)


def _paint_night(
    image: Image.Image,
    base: Rgb,
    rng: random.Random,
    blocked: Blocked,
    area_ratio: float,
) -> tuple[tuple[int, int], ...]:
    width, height = image.size
    pixels = image.load()
    assert pixels is not None
    lifted = (min(base[0] + 6, 255), min(base[1] + 6, 255), min(base[2] + 6, 255))
    for y in range(height):
        for x in range(width):
            distance = abs((y - height) + 0.62 * x) / 60
            if (
                distance < 1
                and bayer4(x, y) < (1 - distance) * 0.22
                and not blocked(x, y, CLEARANCE)
            ):
                pixels[x, y] = lifted

    sky_height = max(height - 30, 1)
    for colours, count in ((FAINT_STARS, 320), (COLOURED_STARS, 48)):
        for _ in range(_scaled(count, area_ratio)):
            x, y = rng.randrange(width), rng.randrange(sky_height)
            if not blocked(x, y, CLEARANCE):
                pixels[x, y] = rng.choice(colours)

    wanted = _scaled(14, area_ratio)
    twinklers: list[tuple[int, int]] = []
    if width > 8 and height > 44:
        for _ in range(wanted * 60):
            if len(twinklers) == wanted:
                break
            x, y = rng.randrange(4, width - 4), rng.randrange(4, height - 40)
            spots = [(x, y), *((x + dx, y + dy) for dx, dy in CROSS_ARMS)]
            if any(blocked(sx, sy, TWINKLER_CLEARANCE) for sx, sy in spots):
                continue
            pixels[x, y] = CROSS_CENTRE
            for dx, dy in CROSS_ARMS:
                pixels[x + dx, y + dy] = CROSS_ARM
            twinklers.append((x, y))
    return tuple(twinklers)


def _paint_day(
    image: Image.Image,
    base: Rgb,
    rng: random.Random,
    blocked: Blocked,
    area_ratio: float,
) -> None:
    width, height = image.size
    pixels = image.load()
    assert pixels is not None
    sun_x, sun_y = SUN_CENTRE

    def put(x: int, y: int, colour: Rgb) -> None:
        if 0 <= x < width and 0 <= y < height and not blocked(x, y, CLEARANCE):
            pixels[x, y] = colour

    for y in range(min(70, height)):
        for x in range(min(70, width)):
            distance = math.hypot(x - sun_x, y - sun_y)
            if distance < 4.5:
                put(x, y, SUN_CORE)
            elif distance < 6:
                put(x, y, SUN_RIM)
            elif distance < 44 and bayer4(x, y) < (1 - (distance - 6) / 38) * 0.6:
                put(x, y, SUN_HALO)
    for degrees in range(0, 360, 45):
        angle = math.radians(degrees)
        for radius in (9, 10, 11):
            put(
                sun_x + round(radius * math.cos(angle)),
                sun_y + round(radius * math.sin(angle)),
                SUN_RAY,
            )

    for y in range(height):
        for x in range(width):
            in_band = (x - 0.7 * y) % 90 < 14
            if in_band and bayer4(x, y) < 0.16 * (1 - y / height) and pixels[x, y] == base:
                put(x, y, LIGHT_BAND)

    _paint_clouds(image, base, rng, blocked, _scaled(7, area_ratio))

    sky_height = max(height - 30, 1)
    for _ in range(_scaled(14, area_ratio)):
        put(rng.randrange(width), rng.randrange(sky_height), SEED)

    for _ in range(_scaled(3, area_ratio)):
        x, y = rng.randrange(width), rng.randrange(sky_height)
        if not any(blocked(x + dx, y + dy, CLEARANCE) for dx, dy in BIRD_SHAPE):
            for dx, dy in BIRD_SHAPE:
                put(x + dx, y + dy, BIRD)


def _paint_clouds(
    image: Image.Image,
    base: Rgb,
    rng: random.Random,
    blocked: Blocked,
    wanted: int,
) -> None:
    width, height = image.size
    if width <= 52 or height <= 72:
        return
    pixels = image.load()
    assert pixels is not None
    placed: list[tuple[int, int]] = []
    for _ in range(4000):
        if len(placed) == wanted:
            break
        cloud_width = rng.randrange(12, 26)
        cx = rng.randrange(cloud_width, width - cloud_width)
        cy = rng.randrange(12, height - 60)
        reach = cloud_width // 2 + 2
        area = [(cx + dx, cy + dy) for dx in range(-reach, reach) for dy in range(-9, 4)]
        if any(blocked(x, y, CLEARANCE) or pixels[x, y] not in (base, LIGHT_BAND) for x, y in area):
            continue
        if any(abs(cx - px) < 40 and abs(cy - py) < 24 for px, py in placed):
            continue
        placed.append((cx, cy))
        for dx in range(-cloud_width // 2, cloud_width // 2):
            for dy in range(-3, 3):
                if (dx / (cloud_width / 2)) ** 2 + (dy / 3.2) ** 2 < 1:
                    pixels[cx + dx, cy + dy] = CLOUD_TOP if dy < 1 else CLOUD_UNDERSIDE
        quarter = cloud_width // 4
        for dx in range(-quarter, quarter):
            for dy in range(-7, -1):
                if (dx / quarter) ** 2 + ((dy + 3) / 3.6) ** 2 < 1:
                    pixels[cx + dx - 2, cy + dy] = CLOUD_TOP
