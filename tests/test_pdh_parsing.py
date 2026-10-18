import pytest
from fakes import PDH_INVALID_HANDLE, PDH_MORE_DATA, PDH_NO_DATA, FakePdh

from power_meter.sensors.adapters import luid_key
from power_meter.sensors.energy_meter import ENERGY_METER_POWER, EnergyMeterCpu, sum_package_watts
from power_meter.sensors.load import CPU_UTILITY, GPU_3D, PdhLoad, gpu_util_by_luid
from power_meter.sensors.pdh import (
    PDH_FMT_DOUBLE,
    PDH_FMT_NOCAP100,
    PdhError,
    PdhNoData,
    PdhQuery,
)

LUID_A = "0x00000000_0x00015061"
LUID_B = "0x00000000_0x0000d1a6"

PROBE_ENERGY_METER = {
    "rapl_package0_pkg": 38470.6,
    "rapl_package0_core0_core": 688.8,
    "_total": 0.0,
}


def engine(pid: int, luid: str, eng: int = 0) -> str:
    return f"pid_{pid}_luid_{luid}_phys_0_eng_{eng}_engtype_3D"


def test_sum_package_watts() -> None:
    assert sum_package_watts(PROBE_ENERGY_METER) == pytest.approx(38.4706)


def test_sum_package_watts_adds_every_package() -> None:
    values = {"rapl_package0_pkg": 30000.0, "rapl_package1_pkg": 25000.0}
    assert sum_package_watts(values) == pytest.approx(55.0)


def test_sum_package_watts_none_without_pkg() -> None:
    assert sum_package_watts({"rapl_package0_core0_core": 688.8, "_total": 0.0}) is None


def test_gpu_util_by_luid() -> None:
    values = {
        engine(1234, LUID_A): 40.0,
        engine(5678, LUID_A): 30.0,
        engine(1234, "0x00000000_0x0000D1A6"): 0.0,
    }
    assert gpu_util_by_luid(values) == pytest.approx({LUID_A: 0.70, LUID_B: 0.0})


def test_gpu_util_by_luid_clamps_sum() -> None:
    values = {engine(1, LUID_A, eng=0): 80.0, engine(2, LUID_A, eng=1): 60.0}
    assert gpu_util_by_luid(values) == {LUID_A: 1.0}


def test_gpu_util_by_luid_skips_names_without_luid() -> None:
    assert gpu_util_by_luid({"_Total": 50.0}) == {}


def test_gpu_luid_matches_dxcore_key() -> None:
    instance = "pid_4321_luid_0x00000000_0x0000D1A6_phys_0_eng_0_engtype_3D"
    assert set(gpu_util_by_luid({instance: 10.0})) == {luid_key(high=0, low=0xD1A6)}


def test_counters_added_with_english_api() -> None:
    dll = FakePdh(
        {
            ENERGY_METER_POWER: PROBE_ENERGY_METER,
            CPU_UTILITY: {"_Total": 5.0},
            GPU_3D: {engine(1, LUID_A): 10.0},
        }
    )
    assert EnergyMeterCpu(lambda: PdhQuery(dll)).open()
    assert PdhLoad(lambda: PdhQuery(dll), discrete_gpus=lambda: None).open()
    assert "PdhAddEnglishCounterW" in dll.calls
    assert "PdhAddCounterW" not in dll.calls


def test_values_fetches_size_then_data_without_capping() -> None:
    dll = FakePdh({CPU_UTILITY: {"_Total": 106.5}})
    query = PdhQuery(dll)
    counter = query.add_english(CPU_UTILITY)
    query.collect()
    assert query.values(counter) == {"_Total": 106.5}
    assert dll.calls.count("PdhGetFormattedCounterArrayW") == 2
    assert dll.formats == [PDH_FMT_DOUBLE | PDH_FMT_NOCAP100] * 2


def test_values_skips_instances_without_valid_data() -> None:
    dll = FakePdh({ENERGY_METER_POWER: dict(PROBE_ENERGY_METER)})
    dll.invalid = {"rapl_package0_core0_core"}
    query = PdhQuery(dll)
    counter = query.add_english(ENERGY_METER_POWER)
    assert query.values(counter) == {"rapl_package0_pkg": 38470.6, "_total": 0.0}


def test_values_empty_without_instances() -> None:
    dll = FakePdh({GPU_3D: {}})
    query = PdhQuery(dll)
    assert query.values(query.add_english(GPU_3D)) == {}


def test_failed_call_raises_with_status() -> None:
    dll = FakePdh({})
    query = PdhQuery(dll)
    with pytest.raises(PdhError, match="PdhAddEnglishCounterW.*0xC0000BB9"):
        query.add_english(CPU_UTILITY)


NO_DATA_STATUSES = {
    "PDH_NO_DATA": 0x800007D5,
    "PDH_CALC_NEGATIVE_DENOMINATOR": 0x800007D6,
    "PDH_CALC_NEGATIVE_TIMEBASE": 0x800007D7,
    "PDH_CALC_NEGATIVE_VALUE": 0x800007D8,
    "PDH_CSTATUS_INVALID_DATA": 0xC0000BBA,
    "PDH_INVALID_DATA": 0xC0000BC6,
}


@pytest.mark.parametrize("status", NO_DATA_STATUSES.values(), ids=NO_DATA_STATUSES.keys())
def test_values_without_valid_data_raise_no_data(status: int) -> None:
    dll = FakePdh({CPU_UTILITY: {"_Total": 1.0}})
    query = PdhQuery(dll)
    counter = query.add_english(CPU_UTILITY)
    dll.array_status[CPU_UTILITY] = status
    with pytest.raises(PdhNoData, match=f"0x{status:08X}"):
        query.values(counter)


def test_collect_without_data_raises_no_data() -> None:
    dll = FakePdh({})
    query = PdhQuery(dll)
    dll.collect_status = PDH_NO_DATA
    with pytest.raises(PdhNoData):
        query.collect()


def test_real_failure_is_not_no_data() -> None:
    dll = FakePdh({CPU_UTILITY: {"_Total": 1.0}})
    query = PdhQuery(dll)
    counter = query.add_english(CPU_UTILITY)
    dll.array_status[CPU_UTILITY] = PDH_INVALID_HANDLE
    with pytest.raises(PdhError) as raised:
        query.values(counter)
    assert not isinstance(raised.value, PdhNoData)


def test_more_data_on_second_call_is_an_error() -> None:
    dll = FakePdh({CPU_UTILITY: {"_Total": 1.0}})
    query = PdhQuery(dll)
    counter = query.add_english(CPU_UTILITY)
    dll.PdhGetFormattedCounterArrayW = lambda *args: PDH_MORE_DATA
    with pytest.raises(PdhError):
        query.values(counter)


def test_close_releases_query_once() -> None:
    dll = FakePdh({})
    query = PdhQuery(dll)
    query.close()
    query.close()
    assert dll.calls.count("PdhCloseQuery") == 1


def test_default_dll_unavailable_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    with pytest.raises(OSError):
        PdhQuery()
