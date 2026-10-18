import json
import logging
import math
import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

from power_meter.meter import Totals
from power_meter.settings import (
    MeterType,
    Settings,
    StandardBand,
    default_settings,
    validate_field,
)

SCHEMA: Final = 1
APP_DIR_NAME: Final = "PCPowerCostMeter"
CONFIG_FILE: Final = "config.json"
STATE_FILE: Final = "state.json"

logger = logging.getLogger(__name__)


def exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.cwd()


def is_writable(directory: Path) -> bool:
    try:
        with tempfile.NamedTemporaryFile(dir=directory):
            return True
    except OSError:
        return False


def resolve_data_dir(primary: Path, fallback_root: Path) -> Path | None:
    if is_writable(primary):
        return primary
    fallback = fallback_root / APP_DIR_NAME
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("Cannot create fallback data folder")
        return None
    return fallback if is_writable(fallback) else None


def atomic_write_json(path: Path, data: Mapping[str, object]) -> None:
    text = json.dumps(data, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _set_aside(path: Path) -> None:
    try:
        os.replace(path, path.with_name(path.name + ".bak"))
    except OSError as exc:
        # The message alone: the full path would put the Windows user name in the log.
        logger.warning(
            "Could not back up unreadable %s: %s", path.name, exc.strerror or type(exc).__name__
        )


def _read_json(path: Path) -> dict[str, object] | None:
    """Returns the stored object, or None if it is missing or was set aside as unusable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Could not read %s: %s", path.name, exc.strerror or type(exc).__name__)
        return None
    except (ValueError, RecursionError):
        _set_aside(path)
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        _set_aside(path)
        return None
    return data


def _number(name: str, raw: object, default: float) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return default
    try:
        if validate_field(name, raw) is not None:
            return default
    except (OverflowError, ValueError):
        return default
    return raw


def _choice[E: StrEnum](kind: type[E], raw: object, default: E) -> E:
    if not isinstance(raw, str):
        return default
    try:
        return kind(raw)
    except ValueError:
        return default


def _theme(raw: object, default: Literal["dark", "light"]) -> Literal["dark", "light"]:
    if raw == "dark":
        return "dark"
    if raw == "light":
        return "light"
    return default


def _flag(raw: object, default: bool) -> bool:
    return raw if isinstance(raw, bool) else default


def _holidays(raw: object, default: tuple[date, ...]) -> tuple[date, ...]:
    if not isinstance(raw, list):
        return default
    try:
        return tuple(date.fromisoformat(item) for item in raw)
    except (TypeError, ValueError):
        return default


def load_settings(data_dir: Path | None, is_laptop: bool) -> Settings:
    defaults = default_settings(is_laptop)
    stored = _read_json(data_dir / CONFIG_FILE) if data_dir else None
    if stored is None:
        return defaults

    def number(name: str) -> float:
        return _number(name, stored.get(name), getattr(defaults, name))

    return Settings(
        meter_type=_choice(MeterType, stored.get("meter_type"), defaults.meter_type),
        standard_band=_choice(StandardBand, stored.get("standard_band"), defaults.standard_band),
        custom_rate=number("custom_rate"),
        tou_peak_rate=number("tou_peak_rate"),
        tou_offpeak_rate=number("tou_offpeak_rate"),
        extra_holidays=_holidays(stored.get("extra_holidays"), defaults.extra_holidays),
        other_w=number("other_w"),
        extra_w=number("extra_w"),
        psu_efficiency=number("psu_efficiency"),
        charge_efficiency=number("charge_efficiency"),
        cpu_tdp_w=number("cpu_tdp_w"),
        gpu_tdp_w=number("gpu_tdp_w"),
        theme=_theme(stored.get("theme"), defaults.theme),
        hours_per_day=int(number("hours_per_day")),
        animate_scene=_flag(stored.get("animate_scene"), defaults.animate_scene),
    )


def save_settings(data_dir: Path, settings: Settings) -> None:
    data = asdict(settings)
    data["extra_holidays"] = [d.isoformat() for d in settings.extra_holidays]
    atomic_write_json(data_dir / CONFIG_FILE, {"schema": SCHEMA, **data})


def _non_negative(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    try:
        value = float(raw)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


@dataclass(frozen=True)
class SavedState:
    totals: Totals
    learned_other_w: float | None = None


def _totals(stored: Mapping[str, object]) -> Totals:
    cost = _non_negative(stored.get("cost_thb"))
    energy = _non_negative(stored.get("energy_kwh"))
    elapsed = _non_negative(stored.get("elapsed_s"))
    if cost is None or energy is None or elapsed is None:
        return Totals()
    return Totals(cost_thb=cost, energy_kwh=energy, elapsed_s=elapsed)


def _learned_watts(raw: object) -> float | None:
    watts = _non_negative(raw)
    if watts is None or validate_field("other_w", watts) is not None:
        return None
    return watts


def load_state(data_dir: Path | None) -> SavedState:
    stored = _read_json(data_dir / STATE_FILE) if data_dir else None
    if stored is None:
        return SavedState(Totals())
    return SavedState(_totals(stored), _learned_watts(stored.get("learned_other_w")))


def save_state(data_dir: Path, state: SavedState) -> None:
    data: dict[str, object] = {"schema": SCHEMA, **asdict(state.totals)}
    if state.learned_other_w is not None:
        data["learned_other_w"] = state.learned_other_w
    atomic_write_json(data_dir / STATE_FILE, data)
