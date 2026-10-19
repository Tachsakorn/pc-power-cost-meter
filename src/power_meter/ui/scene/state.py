import time
from collections.abc import Callable
from enum import StrEnum

from power_meter.power_model import PowerSnapshot
from power_meter.sampler import MeterState

ACTIVE_FROM = 0.20
HIGH_FROM = 0.55
HYSTERESIS = 0.04
MIN_DWELL_S = 5.0


class SceneState(StrEnum):
    DORMANT = "dormant"
    IDLE = "idle"
    ACTIVE = "active"
    HIGH = "high"


_LABELS = {
    SceneState.IDLE: "Resting",
    SceneState.ACTIVE: "Working",
    SceneState.HIGH: "High load",
}


def _load_ratio(snapshot: PowerSnapshot) -> float:
    if snapshot.peak_w > 0:
        return snapshot.wall_w / snapshot.peak_w
    return 0.0


def _target(current: SceneState, ratio: float) -> SceneState:
    """Scene for a load ratio, leaning towards `current` within the hysteresis band."""
    high_from = HIGH_FROM - HYSTERESIS if current is SceneState.HIGH else HIGH_FROM
    if ratio >= high_from:
        return SceneState.HIGH
    active_from = ACTIVE_FROM
    if current in (SceneState.ACTIVE, SceneState.HIGH):
        active_from -= HYSTERESIS
    if ratio >= active_from:
        return SceneState.ACTIVE
    return SceneState.IDLE


class StateTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._current = SceneState.DORMANT
        self._pending: SceneState | None = None
        self._pending_since = 0.0

    @property
    def current(self) -> SceneState:
        return self._current

    def update(self, state: MeterState) -> SceneState:
        if state.status != "running" or state.snapshot is None:
            self._current = SceneState.DORMANT
            self._pending = None
            return self._current

        target = _target(self._current, _load_ratio(state.snapshot))
        if self._current is SceneState.DORMANT:
            self._current = target
            self._pending = None
        elif target is self._current:
            self._pending = None
        elif target is not self._pending:
            self._pending = target
            self._pending_since = self._clock()
        elif self._clock() - self._pending_since >= MIN_DWELL_S:
            self._current = target
            self._pending = None
        return self._current


def describe(scene: SceneState, snapshot: PowerSnapshot | None) -> str:
    if scene is SceneState.DORMANT or snapshot is None:
        return "Waiting for live readings"
    pct = _load_ratio(snapshot) * 100
    return f"{_LABELS[scene]} — {snapshot.wall_w:.0f} W, {pct:.0f}% of this PC's maximum"
