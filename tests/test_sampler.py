import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime
from typing import Any, cast

import pytest

from power_meter import sampler as sampler_module
from power_meter.meter import Meter, Totals
from power_meter.power_model import PowerModel, PowerSnapshot
from power_meter.sampler import Sampler
from power_meter.settings import MeterType, Settings, default_settings
from power_meter.storage import SavedState
from power_meter.tariff import HolidayCalendar

DESKTOP = default_settings(is_laptop=False)
TOU = replace(DESKTOP, meter_type=MeterType.TOU)
WEDNESDAY_10AM = datetime(2026, 10, 14, 10, 0)
WS_PER_KWH = 3_600_000


def wait_until(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met within the timeout")
        time.sleep(0.002)


def snapshot(watts: float) -> PowerSnapshot:
    return PowerSnapshot(
        wall_w=watts,
        parts=(),
        on_battery=False,
        usage_label="Light",
        all_estimated=True,
        peak_w=500.0,
    )


class FakeModel:
    """Blocks in sample() until the test feeds a reading, so each feed is exactly one tick.

    The sampler sits inside sample() between feeds, so a queued command takes effect one
    reading after it is sent.
    """

    def __init__(self) -> None:
        self.feed: queue.Queue[float | Exception] = queue.Queue()
        self.calls = 0
        self.settings: list[Settings] = []
        self.close_calls = 0
        self.learned_other_w: float | None = None
        self._free_running = False
        self._last_w = 0.0

    def sample(self) -> PowerSnapshot:
        self.calls += 1
        if self._free_running and self.feed.empty():
            return snapshot(self._last_w)
        item = self.feed.get(timeout=5.0)
        if isinstance(item, Exception):
            raise item
        self._last_w = item
        return snapshot(item)

    def update_settings(self, settings: Settings) -> None:
        self.settings.append(settings)

    def close(self) -> None:
        self.close_calls += 1

    def release(self) -> None:
        self._free_running = True
        self.feed.put(self._last_w)


class ManualClock:
    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


class Rig:
    def __init__(
        self,
        clock: ManualClock | None = None,
        save: Callable[[SavedState], None] | None = None,
        persisted: Totals | None = None,
        interval_s: float = 0.01,
    ) -> None:
        self.model = FakeModel()
        self.meter = Meter(persisted)
        self.saves: list[SavedState] = []
        self.clock = clock or ManualClock(step=1.0)
        self.sampler = Sampler(
            cast(PowerModel, self.model),
            self.meter,
            DESKTOP,
            HolidayCalendar.bundled(),
            save or self.saves.append,
            interval_s=interval_s,
            autosave_s=30.0,
            now=lambda: WEDNESDAY_10AM,
            mono=self.clock,
        )

    def start(self) -> None:
        self.sampler.start()
        wait_until(lambda: self.model.calls == 1)

    def feed(self, *items: float | Exception) -> None:
        target = self.model.calls + len(items)
        for item in items:
            self.model.feed.put(item)
        wait_until(lambda: self.model.calls == target)

    def stop(self) -> None:
        self.model.release()
        self.sampler.stop()


@pytest.fixture
def make_rig() -> Iterator[Callable[..., Rig]]:
    rigs: list[Rig] = []

    def make(**options: Any) -> Rig:
        rig = Rig(**options)
        rigs.append(rig)
        rig.start()
        return rig

    yield make
    for rig in rigs:
        rig.stop()


def test_warming_up_before_first_tick(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    state = rig.sampler.latest()
    assert state.status == "warming_up"
    assert state.snapshot is None
    assert state.display_w is None
    assert state.totals == Totals()
    assert state.session == Totals()


def test_publishes_running_state(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.feed(120.0, 120.0)
    state = rig.sampler.latest()
    assert state.status == "running"
    assert state.snapshot == snapshot(120.0)
    assert state.totals == rig.meter.totals
    assert state.session == rig.meter.session
    assert state.totals.energy_kwh == pytest.approx(120 / WS_PER_KWH)
    assert state.totals.cost_thb == pytest.approx(120 / WS_PER_KWH * 4.50)


def test_display_w_is_mean_of_last_three(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.feed(100.0)
    assert rig.sampler.latest().display_w == pytest.approx(100.0)
    rig.feed(200.0, 300.0, 400.0)
    state = rig.sampler.latest()
    assert state.display_w == pytest.approx(300.0)
    assert state.snapshot == snapshot(400.0)
    assert state.totals.energy_kwh == pytest.approx((150 + 250 + 350) / WS_PER_KWH)


def test_reset_command_zeroes_totals(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.feed(100.0, 200.0, 300.0)
    assert rig.sampler.latest().totals.cost_thb > 0
    rig.sampler.request_reset()
    rig.feed(400.0)
    assert rig.sampler.latest().totals.cost_thb > 0
    rig.feed(50.0)
    state = rig.sampler.latest()
    assert state.totals == Totals()
    assert state.session == Totals()
    assert state.display_w == pytest.approx(50.0)


def test_reset_is_saved_right_away(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(persisted=Totals(12.5, 2.0, 3600.0))
    rig.sampler.request_reset()
    rig.feed(100.0)
    assert rig.saves == [SavedState(Totals())]


def test_reset_requested_just_before_stop_is_saved(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(persisted=Totals(12.5, 2.0, 3600.0), interval_s=10.0)
    rig.sampler.request_reset()
    rig.stop()
    assert rig.saves
    assert all(saved == SavedState(Totals()) for saved in rig.saves)


def test_saves_carry_the_learned_other_components(make_rig: Callable[..., Rig]) -> None:
    clock = ManualClock()
    rig = make_rig(clock=clock)
    rig.model.learned_other_w = 21.4
    rig.feed(100.0)
    clock.now = 30.0
    rig.feed(100.0)
    assert rig.saves == [SavedState(rig.sampler.latest().totals, learned_other_w=21.4)]


def test_reset_keeps_the_learned_other_components(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(persisted=Totals(12.5, 2.0, 3600.0))
    rig.model.learned_other_w = 21.4
    rig.sampler.request_reset()
    rig.feed(100.0)
    assert rig.saves == [SavedState(Totals(), learned_other_w=21.4)]


def test_apply_settings_switches_tariff(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.feed(100.0)
    rig.sampler.apply_settings(TOU)
    rig.feed(100.0)
    cost_before = rig.sampler.latest().totals.cost_thb
    assert cost_before == pytest.approx(100 / WS_PER_KWH * 4.50)
    rig.feed(100.0)
    cost_after = rig.sampler.latest().totals.cost_thb
    assert cost_after - cost_before == pytest.approx(100 / WS_PER_KWH * 6.38)
    assert rig.model.settings == [TOU]


def test_exception_sets_restarting_then_recovers(
    make_rig: Callable[..., Rig],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(sampler_module, "RESTART_DELAY_S", 0.01)
    rig = make_rig()
    rig.feed(100.0)
    with caplog.at_level(logging.ERROR, logger="power_meter.sampler"):
        rig.feed(RuntimeError("driver went away"))
    state = rig.sampler.latest()
    assert state.status == "restarting"
    assert state.snapshot == snapshot(100.0)
    assert any(record.exc_info for record in caplog.records)
    rig.feed(200.0)
    state = rig.sampler.latest()
    assert state.status == "running"
    assert state.snapshot == snapshot(200.0)
    assert rig.sampler.is_alive()


def test_autosave_called(make_rig: Callable[..., Rig]) -> None:
    clock = ManualClock()
    rig = make_rig(clock=clock)
    rig.feed(100.0)
    clock.now = 29.9
    rig.feed(100.0)
    assert rig.saves == []
    clock.now = 30.0
    rig.feed(100.0)
    assert rig.saves == [SavedState(rig.sampler.latest().totals)]
    clock.now = 45.0
    rig.feed(100.0)
    assert len(rig.saves) == 1


def test_save_now_saves_on_the_sampler_thread(make_rig: Callable[..., Rig]) -> None:
    saved: list[tuple[str, SavedState]] = []
    rig = make_rig(
        clock=ManualClock(),
        persisted=Totals(12.5, 2.0, 3600.0),
        save=lambda state: saved.append((threading.current_thread().name, state)),
    )
    rig.model.release()
    rig.sampler.save_now()
    assert saved == [("sampler", SavedState(Totals(12.5, 2.0, 3600.0)))]


def test_save_now_after_stop_returns_at_once(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.stop()
    started = time.monotonic()
    rig.sampler.save_now()
    assert time.monotonic() - started < 0.5


def test_failed_save_is_logged_and_sampling_continues(
    make_rig: Callable[..., Rig], caplog: pytest.LogCaptureFixture
) -> None:
    def failing_save(state: SavedState) -> None:
        raise OSError("disk full")

    clock = ManualClock()
    rig = make_rig(clock=clock, save=failing_save)
    rig.feed(100.0)
    clock.now = 30.0
    with caplog.at_level(logging.WARNING, logger="power_meter.sampler"):
        rig.feed(100.0, 100.0)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert rig.sampler.latest().status == "running"
    assert rig.sampler.is_alive()


def test_failed_save_log_leaves_out_the_path(
    make_rig: Callable[..., Rig], caplog: pytest.LogCaptureFixture
) -> None:
    def failing_save(state: SavedState) -> None:
        raise OSError(13, "Permission denied", "/home/someone/meter/state.json")

    rig = make_rig(save=failing_save)
    rig.feed(100.0)
    with caplog.at_level(logging.WARNING, logger="power_meter.sampler"):
        rig.stop()
    [record] = caplog.records
    assert "Permission denied" in record.getMessage()
    assert "/home/someone" not in record.getMessage()


def test_stop_saves_and_closes_model(make_rig: Callable[..., Rig]) -> None:
    # A frozen clock, so free-running ticks while stopping can't reach the autosave interval.
    rig = make_rig(clock=ManualClock(step=0))
    rig.feed(100.0, 200.0)
    rig.stop()
    assert not rig.sampler.is_alive()
    assert rig.saves == [SavedState(rig.meter.totals)]
    assert rig.model.close_calls == 1
    rig.sampler.stop()
    assert len(rig.saves) == 1
    assert rig.model.close_calls == 1


def test_stop_returns_promptly_during_restart_delay(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.model.feed.put(RuntimeError("driver went away"))
    wait_until(lambda: rig.sampler.latest().status == "restarting")
    started = time.monotonic()
    rig.sampler.stop(timeout=3.0)
    assert time.monotonic() - started < 1.0
    assert not rig.sampler.is_alive()
