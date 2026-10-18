import logging
import os
import sys
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from power_meter import __version__, single_instance
from power_meter.meter import Meter
from power_meter.power_model import PowerModel
from power_meter.sampler import Sampler
from power_meter.sensors.battery import battery_present
from power_meter.sensors.discovery import build_sources
from power_meter.storage import (
    exe_dir,
    load_settings,
    load_totals,
    resolve_data_dir,
    save_settings,
    save_totals,
)
from power_meter.tariff import HolidayCalendar
from power_meter.ui.app import App, show_already_running

LOG_FILE: Final = "power-meter.log"
LOG_MAX_BYTES: Final = 256 * 1024
LOG_BACKUPS: Final = 2

logger = logging.getLogger("power_meter")


def configure_logging(data_dir: Path | None) -> None:
    handler: logging.Handler = (
        RotatingFileHandler(
            data_dir / LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
        if data_dir is not None
        else logging.NullHandler()
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler],
    )


def saver[T](save: Callable[[Path, T], None], data_dir: Path | None) -> Callable[[T], None]:
    """Binds a save function to the data folder. Without a folder nothing is written."""

    def write(value: T) -> None:
        if data_dir is not None:
            save(data_dir, value)

    return write


def start(data_dir: Path | None) -> App:
    """Loads saved state, starts sampling and builds the window."""
    is_laptop = battery_present()
    settings = load_settings(data_dir, is_laptop)
    totals = load_totals(data_dir)

    sources = build_sources(settings, is_laptop)
    model = PowerModel(sources.chains, sources.battery, settings, refresh=sources.load.refresh)
    holidays = HolidayCalendar.bundled()
    sampler = Sampler(model, Meter(totals), settings, holidays, saver(save_totals, data_dir))
    sampler.start()

    def shut_down() -> None:
        sampler.stop()
        # A sample stuck in a driver call may still be using the counters.
        if not sampler.is_alive():
            sources.load.close()

    return App(sampler, settings, holidays, data_dir, saver(save_settings, data_dir), shut_down)


def main() -> int:
    if not single_instance.acquire():
        show_already_running()
        return 0

    data_dir = resolve_data_dir(exe_dir(), Path(os.environ.get("APPDATA", Path.home())))
    configure_logging(data_dir)
    logger.info("Starting version %s", __version__)
    try:
        app = start(data_dir)
    except Exception:
        # The packaged app has no console; the log is the only place this can show up.
        logger.exception("Startup failed")
        raise
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
