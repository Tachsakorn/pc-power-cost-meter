import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from power_meter import storage
from power_meter.meter import Totals
from power_meter.settings import MeterType, StandardBand, default_settings
from power_meter.storage import (
    APP_DIR_NAME,
    atomic_write_json,
    load_settings,
    load_totals,
    resolve_data_dir,
    save_settings,
    save_totals,
)

DEFAULTS = default_settings(is_laptop=False)


def write_config(directory: Path, **fields: object) -> None:
    (directory / "config.json").write_text(json.dumps({"schema": 1, **fields}))


def test_roundtrip_settings(tmp_path: Path) -> None:
    custom = replace(
        DEFAULTS,
        meter_type=MeterType.TOU,
        standard_band=StandardBand.CUSTOM,
        custom_rate=5.25,
        extra_holidays=(date(2026, 12, 31), date(2027, 1, 2)),
        other_w=40,
        psu_efficiency=0.92,
        theme="light",
        hours_per_day=8,
    )
    save_settings(tmp_path, custom)
    assert load_settings(tmp_path, is_laptop=False) == custom


def test_saved_config_is_readable_json_with_schema(tmp_path: Path) -> None:
    save_settings(tmp_path, replace(DEFAULTS, extra_holidays=(date(2026, 12, 31),)))
    text = (tmp_path / "config.json").read_text()
    data = json.loads(text)
    assert data["schema"] == 1
    assert data["meter_type"] == "standard"
    assert data["extra_holidays"] == ["2026-12-31"]
    assert "\n  " in text


def test_roundtrip_totals(tmp_path: Path) -> None:
    totals = Totals(cost_thb=12.3456, energy_kwh=2.75, elapsed_s=3600.5)
    save_totals(tmp_path, totals)
    assert load_totals(tmp_path) == totals


def test_missing_files_give_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path, is_laptop=True) == default_settings(is_laptop=True)
    assert load_totals(tmp_path) == Totals()


def test_no_data_dir_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(None, is_laptop=False) == DEFAULTS
    assert load_totals(None) == Totals()


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"schema": 2, "other_w": 40}', ""])
def test_corrupt_json_backed_up_and_defaults_used(tmp_path: Path, content: str) -> None:
    (tmp_path / "config.json").write_text(content)
    assert load_settings(tmp_path, is_laptop=False) == DEFAULTS
    assert not (tmp_path / "config.json").exists()
    assert (tmp_path / "config.json.bak").read_text() == content


def test_backup_replaces_older_backup(tmp_path: Path) -> None:
    (tmp_path / "state.json.bak").write_text("old")
    (tmp_path / "state.json").write_text("{broken")
    assert load_totals(tmp_path) == Totals()
    assert (tmp_path / "state.json.bak").read_text() == "{broken"


def test_non_utf8_file_is_treated_as_corrupt(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_bytes(b"\xff\xfe\x00bad")
    assert load_settings(tmp_path, is_laptop=False) == DEFAULTS
    assert (tmp_path / "config.json.bak").exists()


def test_out_of_range_field_falls_back_individually(tmp_path: Path) -> None:
    write_config(tmp_path, psu_efficiency=2.0, other_w=40)
    loaded = load_settings(tmp_path, is_laptop=False)
    assert loaded.psu_efficiency == 0.87
    assert loaded.other_w == 40


def test_field_defaults_follow_the_hardware_profile(tmp_path: Path) -> None:
    write_config(tmp_path, psu_efficiency=2.0)
    assert load_settings(tmp_path, is_laptop=True).psu_efficiency == 0.90


def test_unknown_meter_type_falls_back(tmp_path: Path) -> None:
    write_config(tmp_path, meter_type="solar", standard_band=["151_400"], theme="blue")
    loaded = load_settings(tmp_path, is_laptop=False)
    assert loaded.meter_type is MeterType.STANDARD
    assert loaded.standard_band is StandardBand.UNKNOWN
    assert loaded.theme == "dark"


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    write_config(tmp_path, other_w=40, colour="red")
    assert load_settings(tmp_path, is_laptop=False).other_w == 40


@pytest.mark.parametrize("bad", ["40", None, True, [40], {"w": 40}, float("nan"), float("inf")])
def test_wrong_type_or_non_finite_number_falls_back(tmp_path: Path, bad: object) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"schema": 1, "other_w": bad}))
    assert load_settings(tmp_path, is_laptop=False).other_w == 25


def test_huge_json_integer_falls_back_without_crashing(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        '{"schema": 1, "hours_per_day": 1' + "0" * 400 + ', "other_w": 40}'
    )
    loaded = load_settings(tmp_path, is_laptop=False)
    assert loaded.hours_per_day == 5
    assert loaded.other_w == 40


def test_hours_per_day_accepts_whole_float_and_rejects_fraction(tmp_path: Path) -> None:
    write_config(tmp_path, hours_per_day=8.0)
    loaded = load_settings(tmp_path, is_laptop=False)
    assert loaded.hours_per_day == 8
    assert isinstance(loaded.hours_per_day, int)
    write_config(tmp_path, hours_per_day=7.5)
    assert load_settings(tmp_path, is_laptop=False).hours_per_day == 5


def test_bad_holiday_entry_drops_the_whole_field(tmp_path: Path) -> None:
    write_config(tmp_path, extra_holidays=["2026-12-31", "next tuesday"])
    assert load_settings(tmp_path, is_laptop=False).extra_holidays == ()


def test_holidays_must_be_a_list(tmp_path: Path) -> None:
    write_config(tmp_path, extra_holidays="2026-12-31")
    assert load_settings(tmp_path, is_laptop=False).extra_holidays == ()


@pytest.mark.parametrize(
    "state",
    [
        {"cost_thb": -1.0, "energy_kwh": 1.0, "elapsed_s": 10.0},
        {"cost_thb": 1.0, "energy_kwh": "1", "elapsed_s": 10.0},
        {"cost_thb": 1.0, "energy_kwh": 1.0, "elapsed_s": True},
        {"cost_thb": 1.0, "energy_kwh": 1.0},
        {"cost_thb": 1.0, "energy_kwh": 1.0, "elapsed_s": float("nan")},
    ],
)
def test_invalid_totals_give_zero_totals(tmp_path: Path, state: dict[str, object]) -> None:
    (tmp_path / "state.json").write_text(json.dumps({"schema": 1, **state}))
    assert load_totals(tmp_path) == Totals()


def test_atomic_write_leaves_no_tmp(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"schema": 1, "cost_thb": 1.5})
    atomic_write_json(target, {"schema": 1, "cost_thb": 2.5})
    assert json.loads(target.read_text())["cost_thb"] == 2.5
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_failed_write_keeps_old_file_and_cleans_up(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"schema": 1, "cost_thb": 1.5})
    with pytest.raises(TypeError):
        atomic_write_json(target, {"schema": 1, "cost_thb": object()})
    assert json.loads(target.read_text())["cost_thb"] == 1.5
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_resolve_prefers_writable_primary(tmp_path: Path) -> None:
    primary = tmp_path / "app"
    primary.mkdir()
    assert resolve_data_dir(primary, tmp_path / "roaming") == primary
    assert not (tmp_path / "roaming").exists()


def test_resolve_uses_fallback_when_primary_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary = tmp_path / "program_files"
    primary.mkdir()
    monkeypatch.setattr(storage, "is_writable", lambda directory: directory != primary)
    resolved = resolve_data_dir(primary, tmp_path / "roaming")
    assert resolved == tmp_path / "roaming" / APP_DIR_NAME
    assert resolved.is_dir()


def test_resolve_returns_none_when_nothing_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage, "is_writable", lambda directory: False)
    assert resolve_data_dir(tmp_path, tmp_path / "roaming") is None


def test_resolve_returns_none_when_fallback_cannot_be_created(tmp_path: Path) -> None:
    blocker = tmp_path / "roaming"
    blocker.write_text("a file, not a folder")
    primary = tmp_path / "missing_primary"
    assert resolve_data_dir(primary, blocker) is None


def test_is_writable_reports_real_directories(tmp_path: Path) -> None:
    assert storage.is_writable(tmp_path)
    assert not storage.is_writable(tmp_path / "does_not_exist")
    assert list(tmp_path.iterdir()) == []


def test_exe_dir_uses_executable_folder_when_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage.sys, "frozen", True, raising=False)
    monkeypatch.setattr(storage.sys, "executable", str(tmp_path / "PowerMeter.exe"))
    assert storage.exe_dir() == tmp_path


def test_exe_dir_uses_working_directory_when_not_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(storage.sys, "frozen", raising=False)
    monkeypatch.chdir(tmp_path)
    assert storage.exe_dir() == Path.cwd()
