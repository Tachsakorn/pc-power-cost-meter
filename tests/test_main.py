import logging
from pathlib import Path

import pytest

import power_meter.__main__ as entry


def test_startup_failure_is_logged_and_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def broken_start(data_dir: Path | None) -> None:
        raise RuntimeError("no display")

    monkeypatch.setattr(entry.single_instance, "acquire", lambda: True)
    monkeypatch.setattr(entry, "exe_dir", lambda: tmp_path)
    monkeypatch.setattr(entry, "configure_logging", lambda data_dir: None)
    monkeypatch.setattr(entry, "start", broken_start)

    with caplog.at_level(logging.ERROR, logger="power_meter"), pytest.raises(RuntimeError):
        entry.main()

    [record] = caplog.records
    assert record.getMessage() == "Startup failed"
    assert record.exc_info is not None
