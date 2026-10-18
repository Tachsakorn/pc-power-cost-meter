import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from statistics import fmean
from typing import Final, Literal

from power_meter.meter import Meter, Totals
from power_meter.power_model import PowerModel, PowerSnapshot
from power_meter.settings import Settings
from power_meter.tariff import HolidayCalendar, RateInfo, tariff_from_settings

logger = logging.getLogger(__name__)

RESTART_DELAY_S = 5.0
DISPLAY_SAMPLES: Final = 3

Status = Literal["warming_up", "running", "restarting"]


@dataclass(frozen=True)
class MeterState:
    snapshot: PowerSnapshot | None
    totals: Totals
    session: Totals
    rate: RateInfo
    status: Status
    display_w: float | None
    """Mean of the last few readings, to steady the headline number. Billing uses raw readings."""


class Sampler(threading.Thread):
    """Samples power on its own thread and bills it through the meter.

    Only this thread touches the model and the meter; other threads talk to it through
    latest(), apply_settings(), request_reset() and stop().
    """

    def __init__(
        self,
        model: PowerModel,
        meter: Meter,
        settings: Settings,
        holidays: HolidayCalendar,
        save_totals: Callable[[Totals], None],
        interval_s: float = 1.0,
        autosave_s: float = 30.0,
        now: Callable[[], datetime] = datetime.now,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(name="sampler", daemon=True)
        self._model = model
        self._meter = meter
        self._settings = settings
        self._holidays = holidays
        self._tariff = tariff_from_settings(settings, holidays)
        self._save_totals = save_totals
        self._interval_s = interval_s
        self._autosave_s = autosave_s
        self._now = now
        self._mono = mono
        self._saved_at = mono()
        self._recent_w: deque[float] = deque(maxlen=DISPLAY_SAMPLES)
        self._commands: queue.Queue[Callable[[], None]] = queue.Queue()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._state = MeterState(
            snapshot=None,
            totals=meter.totals,
            session=meter.session,
            rate=self._tariff.rate_at(now()),
            status="warming_up",
            display_w=None,
        )

    def latest(self) -> MeterState:
        with self._lock:
            return self._state

    def apply_settings(self, settings: Settings) -> None:
        self._commands.put(lambda: self._use_settings(settings))

    def request_reset(self) -> None:
        self._commands.put(self._reset)

    def stop(self, timeout: float = 3.0) -> None:
        if self._stopping.is_set():
            return
        self._stopping.set()
        self.join(timeout)
        self._save()
        if self.is_alive():
            # Closing driver handles under a call that is still in flight is unsafe.
            logger.warning("Sampler still busy after %.1f s; leaving sensors open", timeout)
            return
        self._model.close()

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Sampling failed; retrying in %g s", RESTART_DELAY_S)
                with self._lock:
                    self._state = replace(self._state, status="restarting")
                self._stopping.wait(RESTART_DELAY_S)
            else:
                self._stopping.wait(self._interval_s)

    def _tick(self) -> None:
        self._run_commands()
        snap = self._model.sample()
        info = self._tariff.rate_at(self._now())
        mono_s = self._mono()
        self._meter.add(snap.wall_w, mono_s, info.rate)
        self._recent_w.append(snap.wall_w)
        state = MeterState(
            snapshot=snap,
            totals=self._meter.totals,
            session=self._meter.session,
            rate=info,
            status="running",
            display_w=fmean(self._recent_w),
        )
        with self._lock:
            self._state = state
        if mono_s - self._saved_at >= self._autosave_s:
            self._saved_at = mono_s
            self._save()

    def _run_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            command()

    def _use_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._tariff = tariff_from_settings(settings, self._holidays)
        self._model.update_settings(settings)

    def _reset(self) -> None:
        self._meter.reset()
        self._recent_w.clear()

    def _save(self) -> None:
        try:
            self._save_totals(self._meter.totals)
        except OSError as e:
            # The message alone: the full path would put the Windows user name in the log.
            logger.warning("Could not save totals: %s", e.strerror or type(e).__name__)
