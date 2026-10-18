import logging
from dataclasses import replace

import pytest
from fakes import FakeBattery, FakeLoad, FakeSource

from power_meter.power_model import (
    FAILS_BEFORE_DEMOTE,
    RETRY_DEMOTED_S,
    PowerModel,
    PowerSnapshot,
    usage_label,
)
from power_meter.sensors.base import BatteryReading, Component, Reading
from power_meter.sensors.estimators import CpuEstimate, GpuEstimate
from power_meter.settings import Settings, default_settings

DESKTOP = default_settings(is_laptop=False)
LAPTOP = default_settings(is_laptop=True)


def measured(watts: float) -> Reading:
    return Reading(watts, "Meter", True)


def estimate(component: Component, watts: float) -> FakeSource:
    return FakeSource(component, [Reading(watts, "Estimate", False)])


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_model(
    cpu: list[FakeSource] | None = None,
    gpu: list[FakeSource] | None = None,
    settings: Settings = DESKTOP,
    battery: FakeBattery | None = None,
    clock: FakeClock | None = None,
) -> PowerModel:
    return PowerModel(
        {"cpu": cpu or [estimate("cpu", 30)], "gpu": gpu or [estimate("gpu", 0)]},
        battery,
        settings,
        clock=clock or FakeClock(),
    )


def part_watts(snapshot: PowerSnapshot, label: str) -> float:
    return next(p.watts for p in snapshot.parts if p.label == label)


def test_uses_first_available_source() -> None:
    unavailable = FakeSource("cpu", [measured(80)], opens=False)
    available = FakeSource("cpu", [measured(42)])
    snapshot = make_model(cpu=[unavailable, available, estimate("cpu", 30)]).sample()
    assert part_watts(snapshot, "CPU") == 42
    assert unavailable.read_calls == 0


def test_wall_power_formula() -> None:
    snapshot = make_model(
        cpu=[FakeSource("cpu", [measured(50)])], gpu=[FakeSource("gpu", [measured(140)])]
    ).sample()
    assert snapshot.wall_w == pytest.approx((50 + 140 + 25) / 0.87)
    assert [p.label for p in snapshot.parts] == ["CPU", "GPU", "Other", "PSU loss"]
    assert part_watts(snapshot, "PSU loss") == pytest.approx(32.13, abs=0.01)
    assert snapshot.parts[-1].detail == "87% efficiency"
    assert snapshot.parts[0].detail == "measured"
    assert snapshot.parts[2].detail == "estimated"
    assert not snapshot.on_battery


def test_demotes_after_three_failures(caplog: pytest.LogCaptureFixture) -> None:
    flaky = FakeSource("cpu", [measured(50), None])
    model = make_model(cpu=[flaky, estimate("cpu", 30)])
    with caplog.at_level(logging.WARNING):
        watts = [part_watts(model.sample(), "CPU") for _ in range(FAILS_BEFORE_DEMOTE + 2)]
    assert watts == [50, 50, 50, 30, 30]
    assert flaky.read_calls == FAILS_BEFORE_DEMOTE + 1
    assert flaky.close_calls == 1
    assert len(caplog.records) == 1
    assert "FakeSource" in caplog.text


def test_failed_read_reuses_last_good_value() -> None:
    model = make_model(cpu=[FakeSource("cpu", [measured(50), None]), estimate("cpu", 30)])
    model.sample()
    snapshot = model.sample()
    assert part_watts(snapshot, "CPU") == 50
    assert snapshot.parts[0].measured


def test_failed_first_read_uses_next_source() -> None:
    model = make_model(cpu=[FakeSource("cpu", [None]), estimate("cpu", 30)])
    snapshot = model.sample()
    assert part_watts(snapshot, "CPU") == 30
    assert snapshot.parts[0].detail == "estimated"


def test_retry_promotes_after_60s(caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    recovering = FakeSource("cpu", [None, None, None, measured(80)])
    model = make_model(cpu=[recovering, estimate("cpu", 30)], clock=clock)
    with caplog.at_level(logging.WARNING):
        for t in range(FAILS_BEFORE_DEMOTE):
            clock.now = float(t)
            model.sample()
        demoted_at = clock.now

        clock.now = demoted_at + RETRY_DEMOTED_S - 1
        assert part_watts(model.sample(), "CPU") == 30
        assert recovering.open_calls == 1

        clock.now = demoted_at + RETRY_DEMOTED_S
        snapshot = model.sample()
    assert part_watts(snapshot, "CPU") == 80
    assert snapshot.parts[0].measured
    assert recovering.open_calls == 2
    assert part_watts(model.sample(), "CPU") == 80
    assert len(caplog.records) == 2


def test_failed_retry_waits_another_interval() -> None:
    clock = FakeClock()
    broken = FakeSource("cpu", [None])
    model = make_model(cpu=[broken, estimate("cpu", 30)], clock=clock)
    for _ in range(FAILS_BEFORE_DEMOTE):
        model.sample()

    clock.now = RETRY_DEMOTED_S
    assert part_watts(model.sample(), "CPU") == 30
    assert broken.open_calls == 2
    assert broken.close_calls == 2

    clock.now = RETRY_DEMOTED_S * 1.5
    model.sample()
    assert broken.open_calls == 2

    clock.now = RETRY_DEMOTED_S * 2
    model.sample()
    assert broken.open_calls == 3


def test_raising_read_counts_as_failed_read(caplog: pytest.LogCaptureFixture) -> None:
    broken = FakeSource("cpu", [measured(50), OSError("driver reset")])
    model = make_model(cpu=[broken, estimate("cpu", 30)])
    with caplog.at_level(logging.WARNING):
        watts = [part_watts(model.sample(), "CPU") for _ in range(FAILS_BEFORE_DEMOTE + 2)]
    assert watts == [50, 50, 50, 30, 30]
    assert broken.read_calls == FAILS_BEFORE_DEMOTE + 1
    assert broken.close_calls == 1
    assert caplog.text.count("driver reset") == 1
    assert "demoted" in caplog.text


def test_raising_first_read_uses_next_source() -> None:
    model = make_model(cpu=[FakeSource("cpu", [RuntimeError("bad buffer")]), estimate("cpu", 30)])
    assert part_watts(model.sample(), "CPU") == 30


def test_raising_open_uses_next_source(caplog: pytest.LogCaptureFixture) -> None:
    broken = FakeSource("cpu", [measured(80)], opens=OSError("no driver"))
    with caplog.at_level(logging.WARNING):
        model = make_model(cpu=[broken, FakeSource("cpu", [measured(42)]), estimate("cpu", 30)])
    assert part_watts(model.sample(), "CPU") == 42
    assert broken.read_calls == 0
    assert "no driver" in caplog.text


def test_raising_retry_keeps_the_fallback() -> None:
    clock = FakeClock()
    broken = FakeSource("cpu", [None, None, None, OSError("still broken")])
    model = make_model(cpu=[broken, estimate("cpu", 30)], clock=clock)
    for _ in range(FAILS_BEFORE_DEMOTE):
        model.sample()
    clock.now = RETRY_DEMOTED_S
    assert part_watts(model.sample(), "CPU") == 30
    assert broken.open_calls == 2
    assert broken.close_calls == 2


class UnclosableSource(FakeSource):
    def close(self) -> None:
        super().close()
        raise OSError("handle already gone")


def test_raising_close_still_demotes() -> None:
    broken = UnclosableSource("cpu", [None])
    model = make_model(cpu=[broken, estimate("cpu", 30)])
    for _ in range(FAILS_BEFORE_DEMOTE):
        model.sample()
    assert part_watts(model.sample(), "CPU") == 30
    assert broken.close_calls == 1


def test_out_of_range_reading_is_failure() -> None:
    spiking = FakeSource("cpu", [Reading(1500, "Meter", True)])
    model = make_model(cpu=[spiking, estimate("cpu", 30)])
    for _ in range(FAILS_BEFORE_DEMOTE + 1):
        assert part_watts(model.sample(), "CPU") == 30
    assert spiking.read_calls == FAILS_BEFORE_DEMOTE


def test_negative_reading_is_failure() -> None:
    model = make_model(cpu=[FakeSource("cpu", [Reading(-5, "Meter", True)]), estimate("cpu", 30)])
    assert part_watts(model.sample(), "CPU") == 30


def test_battery_path_when_unplugged() -> None:
    battery = FakeBattery(BatteryReading(on_ac=False, rate_w=30))
    snapshot = make_model(settings=LAPTOP, battery=battery).sample()
    assert snapshot.wall_w == pytest.approx(33.33, abs=0.01)
    assert snapshot.on_battery
    assert snapshot.parts[0].label == "Battery"
    assert snapshot.parts[0].watts == 30
    assert snapshot.parts[0].measured
    assert snapshot.parts[1].label == "Charging loss"
    assert snapshot.parts[1].watts == pytest.approx(3.33, abs=0.01)
    assert snapshot.parts[1].detail == "90% efficiency"


def test_battery_ignored_when_on_ac() -> None:
    battery = FakeBattery(BatteryReading(on_ac=True, rate_w=30))
    snapshot = make_model(settings=LAPTOP, battery=battery).sample()
    assert not snapshot.on_battery
    assert snapshot.parts[0].label == "CPU"
    assert snapshot.wall_w == pytest.approx((30 + 0 + 10) / 0.90)


@pytest.mark.parametrize("rate_w", [None, 0.0, -12.0, 1500.0])
def test_battery_ignored_without_valid_rate(rate_w: float | None) -> None:
    battery = FakeBattery(BatteryReading(on_ac=False, rate_w=rate_w))
    assert not make_model(settings=LAPTOP, battery=battery).sample().on_battery


def test_extras_added_after_psu() -> None:
    plain = make_model().sample()
    with_extras = make_model(settings=replace(DESKTOP, extra_w=20)).sample()
    assert with_extras.wall_w - plain.wall_w == pytest.approx(20)
    assert with_extras.parts[-1].label == "Extras"
    assert with_extras.parts[-1].watts == 20
    assert "Extras" not in [p.label for p in plain.parts]


def test_extras_added_on_battery() -> None:
    battery = FakeBattery(BatteryReading(on_ac=False, rate_w=30))
    snapshot = make_model(settings=replace(LAPTOP, extra_w=20), battery=battery).sample()
    assert snapshot.wall_w == pytest.approx(30 / 0.90 + 20)
    assert [p.label for p in snapshot.parts] == ["Battery", "Charging loss", "Extras"]


def test_usage_labels() -> None:
    assert usage_label(10, 100) == "Idle"
    assert usage_label(30, 100) == "Light use"
    assert usage_label(60, 100) == "Heavy use"
    assert usage_label(90, 100) == "Gaming-level use"


def test_peak_uses_tdp_budget_until_exceeded() -> None:
    budget = (65 + 150 + 25) / 0.87
    assert make_model(gpu=[estimate("gpu", 15)]).sample().peak_w == pytest.approx(budget)
    hot = make_model(cpu=[estimate("cpu", 300)], gpu=[estimate("gpu", 400)])
    snapshot = hot.sample()
    assert snapshot.peak_w == pytest.approx(snapshot.wall_w)
    assert snapshot.usage_label == "Gaming-level use"


def test_budget_leaves_out_gpu_tdp_without_discrete_gpu() -> None:
    model = make_model(cpu=[FakeSource("cpu", [measured(7)])], settings=LAPTOP)
    snapshot = model.sample()
    assert snapshot.wall_w == pytest.approx((7 + 10) / 0.90)
    assert snapshot.peak_w == pytest.approx((45 + 10) / 0.90)
    assert snapshot.usage_label != "Idle"


def test_battery_usage_label_without_discrete_gpu() -> None:
    battery = FakeBattery(BatteryReading(on_ac=False, rate_w=31 * 0.90))
    snapshot = make_model(settings=LAPTOP, battery=battery).sample()
    assert snapshot.wall_w == pytest.approx(31)
    assert snapshot.usage_label == "Heavy use"


def test_budget_includes_gpu_tdp_with_measured_idle_gpu() -> None:
    model = make_model(gpu=[FakeSource("gpu", [measured(0)])])
    assert model.sample().peak_w == pytest.approx((65 + 150 + 25) / 0.87)


def test_all_estimated_flag() -> None:
    assert make_model().sample().all_estimated
    gpu_measured = make_model(gpu=[FakeSource("gpu", [measured(100)]), estimate("gpu", 0)])
    assert not gpu_measured.sample().all_estimated


def test_refresh_called_once_per_sample() -> None:
    load = FakeLoad(cpu=0.0)
    calls = 0

    def refresh() -> None:
        nonlocal calls
        calls += 1
        load.cpu = 1.0

    model = PowerModel(
        {"cpu": [CpuEstimate(load, 65)], "gpu": [GpuEstimate(load, 150)]},
        None,
        DESKTOP,
        refresh=refresh,
    )
    snapshot = model.sample()
    model.sample()
    assert calls == 2
    assert part_watts(snapshot, "CPU") == pytest.approx(65)


def test_update_settings_changes_estimator_tdp() -> None:
    load = FakeLoad(cpu=1.0, gpus={"luid": 1.0})
    model = PowerModel(
        {"cpu": [CpuEstimate(load, 65)], "gpu": [GpuEstimate(load, 150)]}, None, DESKTOP
    )
    model.update_settings(replace(DESKTOP, cpu_tdp_w=100, gpu_tdp_w=200))
    snapshot = model.sample()
    assert part_watts(snapshot, "CPU") == pytest.approx(100)
    assert part_watts(snapshot, "GPU") == pytest.approx(200)


def test_close_closes_open_sources() -> None:
    cpu = FakeSource("cpu", [measured(50)])
    battery = FakeBattery(BatteryReading(on_ac=True, rate_w=None))
    model = make_model(cpu=[cpu, estimate("cpu", 30)], battery=battery)
    model.close()
    assert cpu.close_calls == 1
    assert battery.closed
