import pytest

from power_meter.meter import Totals
from power_meter.power_model import PowerSnapshot
from power_meter.sampler import MeterState, Status
from power_meter.ui.scene.state import SceneState, StateTracker, caption, describe


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def snap(wall_w: float, peak_w: float = 100.0) -> PowerSnapshot:
    return PowerSnapshot(
        wall_w=wall_w,
        parts=(),
        on_battery=False,
        usage_label="",
        all_estimated=False,
        peak_w=peak_w,
    )


def reading(ratio: float, status: Status = "running", peak_w: float = 100.0) -> MeterState:
    return MeterState(
        snapshot=snap(ratio * peak_w, peak_w),
        totals=Totals(),
        session=Totals(),
        status=status,
        display_w=ratio * peak_w,
    )


def warming_up() -> MeterState:
    return MeterState(
        snapshot=None,
        totals=Totals(),
        session=Totals(),
        status="warming_up",
        display_w=None,
    )


def tracker_at(scene_ratio: float) -> tuple[StateTracker, FakeClock]:
    clock = FakeClock()
    tracker = StateTracker(clock)
    tracker.update(reading(scene_ratio))
    return tracker, clock


def test_starts_dormant_until_running() -> None:
    tracker = StateTracker(FakeClock())

    assert tracker.current is SceneState.DORMANT
    assert tracker.update(warming_up()) is SceneState.DORMANT


def test_first_reading_shows_at_once() -> None:
    tracker = StateTracker(FakeClock())

    assert tracker.update(reading(0.6)) is SceneState.HIGH
    assert tracker.current is SceneState.HIGH


def test_new_state_must_hold_five_seconds() -> None:
    tracker, clock = tracker_at(0.05)
    assert tracker.current is SceneState.IDLE

    for t in (0.0, 1.0, 2.5, 4.9):
        clock.now = t
        assert tracker.update(reading(0.7)) is SceneState.IDLE

    clock.now = 5.0
    assert tracker.update(reading(0.7)) is SceneState.HIGH


def test_hysteresis_keeps_active_near_the_boundary() -> None:
    tracker, clock = tracker_at(0.3)
    assert tracker.current is SceneState.ACTIVE

    for t in (1.0, 5.0, 10.0):
        clock.now = t
        assert tracker.update(reading(0.17)) is SceneState.ACTIVE

    for t in (11.0, 15.9):
        clock.now = t
        assert tracker.update(reading(0.15)) is SceneState.ACTIVE
    clock.now = 16.0
    assert tracker.update(reading(0.15)) is SceneState.IDLE


def test_going_back_resets_the_dwell() -> None:
    tracker, clock = tracker_at(0.3)

    for t, ratio in ((0.0, 0.7), (3.0, 0.7), (3.5, 0.3), (4.0, 0.3), (4.5, 0.7), (7.5, 0.7)):
        clock.now = t
        assert tracker.update(reading(ratio)) is SceneState.ACTIVE

    clock.now = 9.5
    assert tracker.update(reading(0.7)) is SceneState.HIGH


def test_restarting_is_dormant_immediately() -> None:
    tracker, _ = tracker_at(0.7)
    assert tracker.current is SceneState.HIGH

    assert tracker.update(reading(0.7, status="restarting")) is SceneState.DORMANT


@pytest.mark.parametrize("peak", [0.0, float("nan")])
def test_bad_peak_counts_as_idle(peak: float) -> None:
    tracker = StateTracker(FakeClock())

    assert tracker.update(reading(0.5, peak_w=peak)) is SceneState.IDLE


def test_describe_reads_like_the_panel() -> None:
    assert (
        describe(SceneState.HIGH, snap(wall_w=214.4, peak_w=276))
        == "High load — 214 W, 78% of this PC's maximum"
    )
    assert (
        describe(SceneState.IDLE, snap(wall_w=60, peak_w=300))
        == "Resting — 60 W, 20% of this PC's maximum"
    )
    assert (
        describe(SceneState.ACTIVE, snap(wall_w=90, peak_w=300))
        == "Working — 90 W, 30% of this PC's maximum"
    )
    assert describe(SceneState.DORMANT, None) == "Waiting for live readings"


def test_describe_without_a_reading_waits() -> None:
    assert describe(SceneState.HIGH, None) == "Waiting for live readings"


def test_caption_names_state_and_live_status() -> None:
    assert caption(SceneState.ACTIVE) == "Active · following live data"
    assert caption(SceneState.IDLE) == "Idle · following live data"
    assert caption(SceneState.HIGH) == "High load · following live data"
    assert caption(SceneState.DORMANT) == "Waiting for live data"
