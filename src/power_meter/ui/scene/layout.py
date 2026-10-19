import json
from dataclasses import dataclass

Point = tuple[int, int]
Route = tuple[Point, ...]
Spot = tuple[int, int, int]


def _route(points: list[list[int]]) -> Route:
    return tuple((x, y) for x, y in points)


def _spot(point: list[int]) -> Spot:
    x, y, depth = point
    return x, y, depth


@dataclass(frozen=True)
class Layout:
    """Where things are in the scene, in native pixels. A spot's third value is how close it
    is to the viewer, on the same scale as the depth map."""

    routes: tuple[Route, ...]
    core: Point
    log: Route
    rabbit_loop: tuple[Spot, ...]
    rabbit_bed: Spot
    air_band: tuple[int, int]

    @classmethod
    def parse(cls, text: str) -> "Layout":
        raw = json.loads(text)
        cx, cy = raw["core"]
        top, bottom = raw["air_band"]
        return cls(
            routes=tuple(_route(r) for r in [*raw["roots"], raw["trunk"], *raw["branches"]]),
            core=(cx, cy),
            log=_route(raw["log"]),
            rabbit_loop=tuple(_spot(p) for p in raw["rabbit_loop"]),
            rabbit_bed=_spot(raw["rabbit_bed"]),
            air_band=(top, bottom),
        )
