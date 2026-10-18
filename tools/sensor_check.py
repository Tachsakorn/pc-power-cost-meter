"""Prints what every power source reads on this PC, once a second for 10 seconds."""

import logging
import time

from power_meter.sensors.base import Component, PowerSource
from power_meter.sensors.battery import battery_present
from power_meter.sensors.discovery import build_sources
from power_meter.settings import default_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    is_laptop = battery_present()
    sources = build_sources(default_settings(is_laptop), is_laptop)

    available: list[tuple[Component, PowerSource]] = []
    for component, chain in sources.chains.items():
        for source in chain:
            if source.open():
                available.append((component, source))
            else:
                print(f"{component}  {type(source).__name__}  unavailable")
    battery = sources.battery
    if battery is not None and not battery.open():
        print("battery  unavailable")
        battery = None

    try:
        for _ in range(10):
            time.sleep(1)
            sources.load.refresh()
            for component, source in available:
                reading = source.read()
                if reading is None:
                    print(f"{component}  {type(source).__name__}  no reading")
                else:
                    kind = "measured" if reading.measured else "estimated"
                    print(f"{component}  {reading.source}  {kind}  {reading.watts:.1f} W")
            print(f"gpu  util by adapter: {dict(sources.load.gpu_utilizations())}")
            if battery is not None:
                status = battery.read()
                if status is None:
                    print("battery  no reading")
                else:
                    print(f"battery  on AC: {status.on_ac}  discharge: {status.rate_w} W")
            print()
    finally:
        for _, source in available:
            source.close()
        if battery is not None:
            battery.close()
        sources.load.close()


if __name__ == "__main__":
    main()
