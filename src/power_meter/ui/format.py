from datetime import datetime

from power_meter.settings import MeterType, Settings, StandardBand
from power_meter.tariff import Period, RateInfo

SHORT_FORM_FROM = 100


def amount(value: float) -> str:
    return f"{value:,.4f}"


def baht_short(value: float) -> str:
    return f"฿{value:,.0f}" if round(value, 2) >= SHORT_FORM_FROM else f"฿{value:,.2f}"


def duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under 1 min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def watts(value: float) -> str:
    return f"{value:.0f} W"


def price_line(settings: Settings, info: RateInfo, *, now: datetime | None = None) -> str:
    if settings.meter_type is MeterType.STANDARD:
        suffix = " (default)" if settings.standard_band is StandardBand.UNKNOWN else ""
        return f"Normal meter · ฿{info.rate:.2f} per unit{suffix}"

    current = now or datetime.now()
    if info.period is Period.PEAK:
        label, upcoming = "Peak", "Off-peak"
    else:
        label, upcoming = "Off-peak", "Peak"
    line = f"TOU · {label} ฿{info.rate:.2f} now"
    if info.next_change is None:
        return line
    when = info.next_change
    day = "" if when.date() == current.date() else f"{when:%a} "
    return f"{line} · {upcoming} from {day}{when:%H:%M}"
