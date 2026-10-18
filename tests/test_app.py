from importlib import resources

from power_meter.power_model import Part, PowerSnapshot
from power_meter.ui.app import measured_components


def snapshot(*parts: Part, on_battery: bool = False) -> PowerSnapshot:
    return PowerSnapshot(
        wall_w=100.0,
        parts=parts,
        on_battery=on_battery,
        usage_label="Light use",
        all_estimated=False,
        peak_w=300.0,
    )


def test_measured_components_follow_the_cpu_and_gpu_parts() -> None:
    snap = snapshot(
        Part("CPU", 40.0, True, "measured"),
        Part("GPU", 20.0, False, "estimated"),
        Part("Other", 25.0, False, "estimated"),
    )

    assert measured_components(snap) == {"cpu": True, "gpu": False}


def test_nothing_counts_as_measured_while_warming_up() -> None:
    assert measured_components(None) == {"cpu": False, "gpu": False}


def test_nothing_counts_as_measured_on_battery() -> None:
    snap = snapshot(
        Part("Battery", 30.0, True, "measured"),
        Part("Charging loss", 3.0, False, "90% efficiency"),
        on_battery=True,
    )

    assert measured_components(snap) == {"cpu": False, "gpu": False}


def test_window_icon_ships_with_the_package() -> None:
    icon = resources.files("power_meter").joinpath("data/icon.ico").read_bytes()

    assert icon[:4] == b"\x00\x00\x01\x00"
