import logging

import pytest

from power_meter.sensors.adapters import AdapterInfo, discrete_gpu_luids, discrete_luids, luid_key

E_NOINTERFACE = 0x80004002


class FailingDxcore:
    def __init__(self) -> None:
        self.calls = 0

    def DXCoreCreateAdapterFactory(self, riid: object, factory: object) -> int:
        self.calls += 1
        return E_NOINTERFACE


def test_luid_key_is_high_then_low_part() -> None:
    assert luid_key(high=0, low=0x15061) == "0x00000000_0x00015061"
    assert luid_key(high=1, low=0xD1A6) == "0x00000001_0x0000d1a6"


def test_luid_key_treats_high_part_as_unsigned() -> None:
    assert luid_key(high=-1, low=0) == "0xffffffff_0x00000000"


def test_discrete_luids_skip_integrated_and_software_adapters() -> None:
    adapters = [
        AdapterInfo(luid_key(0, 0x15061), is_hardware=True, is_integrated=False),
        AdapterInfo(luid_key(0, 0xD1A6), is_hardware=True, is_integrated=True),
        AdapterInfo(luid_key(0, 0xE2B0), is_hardware=False, is_integrated=False),
    ]
    assert discrete_luids(adapters) == frozenset({"0x00000000_0x00015061"})


def test_none_when_factory_creation_fails(caplog: pytest.LogCaptureFixture) -> None:
    dxcore = FailingDxcore()
    with caplog.at_level(logging.WARNING):
        assert discrete_gpu_luids(dxcore) is None
    assert dxcore.calls == 1
    assert len(caplog.records) == 1
    assert "0x80004002" in caplog.text


def test_none_when_dxcore_lacks_the_factory_export() -> None:
    assert discrete_gpu_luids(object()) is None


def test_none_when_dxcore_cannot_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert discrete_gpu_luids() is None
