# PC Power & Cost Meter: Design

- **Date:** 2026-10-18
- **Repo:** `pc-power-cost-meter` (MIT)

## 1. Purpose

A small Windows desktop app that shows, in real time, how much electricity a PC uses and what it costs in Thai Baht. It is aimed at ordinary users first (download, double-click, see money ticking) and is also a portfolio project for a university application, so the engineering behind the numbers has to be visible and defensible.

### Success criteria

- A first-time user opens the `.exe` and sees a cost ticking up within seconds, with zero configuration.
- The number is as accurate as a standard (non-admin) user account allows, and the app is honest about which parts are measured and which are estimated.
- A reviewer opening the repo can see the formula, the design decisions, passing tests, and a reproducible build.

### Hard constraints

- 100% offline. No telemetry, no network calls, no update checks.
- No administrator rights and no UAC prompt.
- No external helper software, such as LibreHardwareMonitor.
- Only documented OS and vendor APIs. No registry hacks, no kernel drivers.
- Single-file `.exe` built with `pyinstaller --onefile --noconsole`.
- English UI. Currency defaults to Thai Baht.

### Non-goals (v1)

- Usage history and charts
- Multiple UI languages
- An installer or auto-update
- Measuring the monitor or other devices plugged in separately (the user can add a fixed wattage instead)
- Tracking time while the app is closed

## 2. Hardware evidence

Measured on a desktop with a Ryzen 5 5600, RX 7600 and B450M board, running Windows 11 25H2 and Python 3.15.0 under a standard user account. `tools/sensor_check.py` repeats the CPU, GPU, utilisation and battery readings on any PC.

| Source | Result | Idle | Load |
|---|---|---|---|
| Windows *Energy Meter* PDH counter `rapl_package0_pkg` (CPU package power, reported in mW) | Works without admin | ~39 W | ~76.6 W all-core |
| AMD ADL `ADL2_New_QueryPMLogData_Get` (OverDrive 8) | Works, ~1.4 ms per call | sensor #73 ≈ 6–7 | sensor #73 ≈ 129–144 in-game |
| PDH `% Processor Utility` / `% Processor Performance` | Works | 2–6 % | 106 % (frequency-scaled) |
| PDH `GPU Engine(*engtype_3D)` utilization | Works | 0 % | tracks game load |
| NVML | Not present on desktop | n/a | n/a |
| Battery IOCTL | No battery on desktop | n/a | n/a |

**Sensor #73 is `PMLOG_BOARD_POWER`** (total board power in watts), as defined in the ADL SDK's `adl_defines.h`. Its readings track GPU activity and stay within the RX 7600's ~165 W board power, and `tools/sensor_check.py` reads the GPU as measured through it on the same desktop.

**Not yet verified:** a laptop (Gigabyte, Ryzen AI, RTX 5070 Laptop). Untested there: NVML on a hybrid-graphics laptop, the battery discharge rate, and Energy Meter on Ryzen AI. Each of these has a fallback, and they must be verified before v1.0 ships (see §11).

## 3. Architecture

Layers, each with a single job. Only `sensors/`, `single_instance.py` and `ui/` touch Windows or Tk; everything else is pure Python and unit-testable on any OS. Windows DLLs are loaded inside `open()` or a function, never at import time, so every module imports on Linux.

```
src/power_meter/
  __main__.py          entry point: logging, single-instance check, wiring, main loop
  single_instance.py   named mutex so only one copy runs
  sensors/
    base.py            Reading, BatteryReading; PowerSource, BatterySource, LoadProvider protocols
    warn_once.py       logs a repeating sensor fault once instead of every second
    pdh.py             thin ctypes wrapper over pdh.dll (English counter names)
    load.py            CPU utilisation and per-adapter GPU 3D utilisation from PDH
    adapters.py        DXCore: which GPU adapters are discrete hardware
    energy_meter.py    CPU package watts (RAPL via Windows Energy Meter)
    amd_adl.py         AMD GPU board power (ADL PMLog)
    nvidia_nvml.py     NVIDIA GPU power (nvidia-ml-py)
    battery.py         battery presence and discharge rate (SetupAPI + IOCTL_BATTERY_*)
    estimators.py      utilisation-based fallbacks for CPU and GPU
    discovery.py       builds the source chains for this PC
  power_model.py       picks the best source per component, returns a PowerSnapshot
  tariff.py            Normal and TOU pricing, holiday calendar
  meter.py             integrates watts into units (kWh) and Baht
  projection.py        monthly cost estimate
  settings.py          Settings dataclass, defaults, validation ranges
  storage.py           config.json / state.json load, atomic save, data-folder fallback
  sampler.py           background thread: sample, bill, publish MeterState; commands; autosave
  data/
    holidays_th.json   bundled off-peak holidays
    icon.ico           window and exe icon
    scene/             dome scene layers (back, canopy, glass PNGs) and particle paths
  ui/
    app.py             main window: polling, view switching, theme, saving settings
    stage.py           window-filling canvas: backdrop, animated scene, caption, hover text
    main_view.py       dashboard and its view-model
    gauge.py           tick gauge image
    settings_view.py   settings view
    form.py            settings form parsing and validation (no Tk)
    format.py          money, duration, watt and price-line text
    theme.py           design tokens and the contrast-ratio calculation
    widgets.py         shared widgets: buttons, segmented control, dropdown, field, scroll area
    scene/
      state.py         scene state from the live load: thresholds, hysteresis, dwell
      art.py           composes the layers and draws particles, rings and haze per frame
      palette.py       colour roles and their per-state and daylight overrides
      backdrop.py      starry and daytime pixel backdrops
      motion.py        reads Windows' "Animation effects" setting
      dither.py        ordered-dither threshold
packaging/             PyInstaller spec (builds the Windows version resource), launcher script
tests/
tools/sensor_check.py  prints what every power source reads on this PC
```

### Data flow

```
sampler thread (every 1.0 s, monotonic clock)
  run queued commands (new settings, reset)
  power_model.sample() ──► PowerSnapshot(wall_w, parts, on_battery, usage_label, peak_w)
  tariff.rate_at(now)  ──► RateInfo(rate, period, next_change)
  meter.add(wall_w, mono_s, rate)
  publish MeterState(snapshot, totals, session, status, display_w)   (latest only, under a lock)
  every 30 s: save totals

Tk main loop (after() every 250 ms)
  tariff.rate_at(now) for the window's current settings ──► RateInfo
  sampler.latest() + settings + RateInfo ──► build_view_model() ──► MainView.render(view_model)
  sampler.latest() ──► StateTracker.update() ──► Stage.show_scene(state, caption, hover text)

Stage (after() every 125 ms, while the window is visible and animation is on)
  draws the next scene frame
```

- Hardware calls never run on the Tk thread, so a slow or hung driver call cannot freeze the window.
- Only the sampler thread touches the power model and the meter. The UI reads immutable `MeterState` objects through `latest()` and talks back through `apply_settings()`, `request_reset()` and `save_now()`, which queue commands for the sampler's next tick. Commands still queued when the sampler stops run before the final save.
- `build_view_model` is a pure function that formats everything the main view shows, including the monthly projection, so the view itself only sets text. The scene's state logic (`StateTracker`, `describe`, `caption`) is likewise free of Tk and takes an injectable clock.
- The window prices the display from its own copy of the settings rather than the sampler's, so right after a settings change the price line and cost per hour never mix the new meter type with the old rate. Billing switches on the sampler's next tick.

## 4. Power model

### Source interface

```python
@dataclass(frozen=True)
class Reading:
    watts: float
    source: str      # e.g. "Energy Meter (RAPL)", "AMD ADL", "Estimate"
    measured: bool

class PowerSource(Protocol):
    component: Literal["cpu", "gpu"]
    def open(self) -> bool: ...          # False if unavailable on this PC
    def read(self) -> Reading | None: ...  # None on a transient failure
    def close(self) -> None: ...
```

The battery is not a `PowerSource`: it replaces the whole component sum rather than one component, so `PowerModel` reads it separately.

### Selection order (chosen at startup; re-evaluated on failure)

| Component | Measured, in order | Fallback (estimated) |
|---|---|---|
| CPU | Energy Meter `*_pkg` instances, summed (mW → W) | `cpu_tdp × (0.30 + 0.70 × util)` |
| GPU | ADL (AMD), then NVML (NVIDIA); each sums all of its vendor's discrete GPUs | `gpu_tdp × (0.10 + 0.90 × util_3d)` of the busiest discrete adapter |
| Whole laptop (on battery only) | battery discharge rate | the component sum above |

- `util` is PDH `% Processor Utility / 100`, clamped to 0–1. `util_3d` is the 3D engine utilisation summed per adapter LUID (PDH lists one instance per process), clamped to 0–1.
- Only discrete hardware adapters count towards `util_3d`. DXCore classifies the adapters once at startup (`IsHardware` and not `IsIntegrated`); an integrated GPU's power is already part of the CPU package reading, so estimating it too would count it twice. If DXCore can't classify the adapters, every adapter counts. When DXCore finds no discrete adapter and no measured GPU source is active, the GPU row of the breakdown reads "included in CPU". An estimate of 0 W alone doesn't mean there is no discrete GPU: the load data can also be empty because the counters are missing or stale.
- The estimate uses the busiest discrete adapter only, because summing would add an idle floor for every idle card. With several discrete GPUs and no measured GPU source, it reads low.
- A source that fails 3 reads in a row is demoted to the next one in its chain. Every 60 s the best demoted source is retried, and promoted back if it returns a valid reading.
- A source whose `open()`, `read()` or `close()` raises is treated as unavailable or as a failed read, and the error is logged once.
- Sanity limits: readings below 0 W or above 1000 W are discarded as a failed read. After a failed read the last good reading is reused until the source is demoted.
- PDH rate counters have no valid value for a round in which one of their instances appears, exits or restarts (`PDH_NO_DATA`, `PDH_CALC_NEGATIVE_*`, `PDH_INVALID_DATA`, `PDH_CSTATUS_INVALID_DATA`). GPU Engine instances belong to processes, so this happens whenever a game starts or crashes. Such a round is not logged as a failure: each utilisation value keeps its last reading, and is cleared after 5 refreshes in a row without a new one so the estimators never run on stale load. The Energy Meter returns its last reading if it is at most 5 s old, so a single empty round never demotes RAPL.

### Whole-PC wall power

```
P_dc   = P_cpu + P_gpu + P_other
P_wall = P_dc / η_psu + P_extra
```

On battery (laptop unplugged, valid discharge rate):

```
P_wall = |P_battery| / η_charge + P_extra
```

The energy drawn from the battery is paid for later, when it is recharged, so charging losses count.

**Learned `P_other` (laptops).** On battery, every sample also yields

```
residual = clamp(P_battery − P_cpu − P_gpu(discrete only), 0, 150 W)
```

The discrete GPU term follows the same presence rule as the breakdown: it is 0 when DXCore found no discrete GPU and no GPU is measured. Screen, storage, Wi-Fi, RAM and any CPU under-reporting all land in the residual. It is smoothed with an exponential moving average, `α = Δt / (120 s + Δt)`, with `Δt` from the model's monotonic clock between consecutive battery samples; samples with `Δt ≤ 0` or `Δt > 5 s` are skipped, and time plugged in breaks the chain. The average starts from zero and is divided by its accumulated weight `1 − Π(1 − α)`, so the first minute is a time-weighted mean of its samples rather than dominated by the first one. The value becomes usable after 60 s of accumulated battery samples and keeps updating on battery after that.

Once usable, and only while `other_w` still equals the default of the detected profile, the learned value replaces `P_other`: plugged in, in `P_dc` (shown as `Other … learned on battery`) and in `P_peak`; on battery, in `P_peak` only, so the usage level doesn't jump at unplug. A value set under Advanced settings wins everywhere. Whether the user has overridden `other_w` is judged against the detected profile's default (system battery reported at startup), not inferred from whether the battery source opens. Desktops never learn: they have no battery, and a learned value in `state.json` is ignored without the laptop profile.

| Parameter | Desktop default | Laptop default | Range |
|---|---|---|---|
| `P_other` (board, RAM, storage, fans) | 25 W | 10 W | 0–150 W |
| `η_psu` (PSU / adapter efficiency) | 87 % | 90 % | 50–99 % |
| `η_charge` | n/a | 90 % | 50–99 % |
| `P_extra` (monitor, speakers: user-added) | 0 W | 0 W | 0–500 W |
| `cpu_tdp` (fallback only) | 65 W | 45 W | 5–400 W |
| `gpu_tdp` (fallback only) | 150 W | 100 W | 0–600 W |

The desktop/laptop profile is chosen by whether Windows reports a system battery. Every value except `η_charge` can be changed under Advanced settings; `η_charge` is read from `config.json`.

### Usage level (plain-language label)

`ratio = P_wall / P_peak`, where `P_peak = max(observed max this session, (cpu_tdp + gpu_tdp + P_other) / η_psu + P_extra)`.

`gpu_tdp` is left out of `P_peak` only when DXCore classified the adapters, found no discrete GPU, and no measured GPU source is active. An integrated GPU draws from the CPU's power budget, so counting `gpu_tdp` would make a busy integrated-only laptop read as idle. If the adapter types are unknown, `gpu_tdp` stays in.

| ratio | Label |
|---|---|
| < 0.20 | Idle |
| < 0.45 | Light use |
| < 0.75 | Heavy use |
| ≥ 0.75 | Gaming-level use |

## 5. Energy and cost

### Integration (trapezoidal rule, every sample)

```
Δt     = t_now − t_prev                    (monotonic seconds)
ΔE_kWh = (P_prev + P_now) / 2 × Δt / 3 600 000
cost  += ΔE_kWh × rate(wall_clock_now)
```

If `Δt > 5 s` (system sleep, suspended thread, clock jump), the interval is skipped and not billed. The meter resynchronises on the next sample.

### Tariffs

All rates are all-in Baht per unit: `(energy charge + Ft) × 1.07 VAT`. Defaults use the tariff effective from the September 2026 bill (Ft = +0.1623 THB/unit). The TOU rates match PEA's published tariff, and the normal-band rates come from the residential energy charges in effect from the September 2026 bill. Ft changes every four months, so the defaults need rechecking each time it does. The fixed monthly service charge is excluded, because it does not change with PC usage.

**Standard (progressive) meter.** The app cannot know total household usage, so the user chooses their household band from the bill. The PC is priced at the household's **marginal** tier: switching the PC off removes units from the top tier the house reaches.

| Option | Rate |
|---|---|
| I don't know (default) | 4.50 |
| Up to 200 units/month | 3.38 |
| 201–400 units/month | 4.62 |
| Over 400 units/month | 4.84 |
| Custom | user value, 0.01–100 |

Known limitation: if the PC's own usage pushes the house across a tier boundary, part of it is priced at the wrong tier. The README lists this under its limitations.

**TOU meter**

| Period | When | Default rate |
|---|---|---|
| Peak | Mon–Fri 09:00–21:59:59 | 6.38 |
| Off-peak | 22:00–08:59:59, Sat, Sun, listed holidays | 3.00 |

- TOU energy charges are the MEA/PEA residential TOU rates for low-voltage supply (PEA 1.2.2 below 22 kV, MEA 1.3.2 below 12 kV): 5.7982 peak and 2.6369 off-peak before Ft and VAT.
- Off-peak all day: Saturday, Sunday, National Labour Day and the regular public holidays (วันหยุดราชการตามปกติ). Substitution days, the Royal Ploughing Ceremony and one-off special holidays declared by the Cabinet follow the normal weekday schedule.
- Holidays come from `data/holidays_th.json` (`{"source": [...], "years": {"2026": ["2026-12-05", …], "2027": [...]}}`), bundled into the exe, plus optional user additions in `config.json`.
- The local system clock and time zone are trusted as-is.

Rates and holidays never update automatically; that would require network access. The README explains how to change the rates.

### Monthly projection

```
avg_cost_per_hour = session_cost / session_hours
monthly           ≈ avg_cost_per_hour × hours_per_day × 30
```

- Shown only after 60 s of session data. Before that it reads "Measuring…".
- `hours_per_day` defaults to 5, range 1–24, and is persisted.
- "Session" means since the app started, so the projection follows current behaviour rather than the all-time total.

## 6. UI

Goals: understandable by non-technical users, with the technical detail always one glance away, and an identity of its own rather than a stock dashboard look. The window pairs the numbers with a small pixel-art scene that reacts to the live load.

### Window and layout

The window is fixed at 760 × 600 logical px (customtkinter's scaling applies). A single `Stage` canvas fills it. The left column, 300 px wide, holds the scene; the right column, 460 px wide, holds the dashboard or, when open, Settings, so the scene stays visible in both.

```
 [dome scene]       ● Measuring                      ☀  ⚙
                           ╭ tick gauge ╮
                              247 W
                            Gaming-level use
                     0                    420 W
 ● High load ·       SPENT
   following         ฿ 1.2847                  in 2h 15m
   live data         ───────────────────────────────────
                     PER HOUR   │ PER MONTH   │ HOURS A DAY
                     ฿1.11      │ ≈ ฿166      │ [−] 5 [+]
                     ───────────────────────────────────
                     BREAKDOWN
                     CPU       ▬▬▬▬▬▬     50 W  ● measured
                     GPU       ▬▬▬▬▬▬▬▬  140 W  ● measured
                     Other     ▬▬         25 W  ○ estimated
                     PSU loss  ▬▬         32 W  87% efficiency
                     ───────────────────────────────────
                     Normal meter · ฿4.50 per unit   Change   Reset
```

- **Status line.** "● Measuring", or the active notice (see States). The theme and Settings buttons are square icon buttons at the right end, using Segoe Fluent Icons or Segoe MDL2 Assets when installed and text symbols otherwise.
- **Gauge.** Watts in a large light weight with a small "W", the usage label below, and "0" and the PC's peak watts (`P_peak`) at the ends of the scale. With TOU selected, the price line in the footer reads `TOU · Peak ฿6.38 now · Off-peak from 22:00`.
- **Spent.** The "฿" sign small, the total large in the light weight with four decimals, and "in <duration>" right-aligned on the same baseline. Billing uses raw samples; the displayed watts are smoothed over the last 3 samples.
- **Stats row.** Per hour, per month and hours a day (a 1–24 stepper that saves on every change), separated by 1 px rules. The month figure reads "Measuring…" for the first minute of session data (§5).
- **Breakdown.** One row per part: name, a thin bar showing its share of the largest part, watts, and a dot with the detail text. A filled dot marks a measured part and a hollow one an estimate. Details are always shown; the taller window fits them, so there is no toggle.
- **Footer.** The price line and **Change** on the left, **Reset** on the right. Reset asks for confirmation by changing to "Click again to reset" for 3 s, and ignores a second click within 0.4 s of the first. No modal dialog.
- Copy is short and label-style. Pixel art is never used for text or values.
- The theme button switches between dark and light at once and saves the choice.

### Gauge

61 ticks on a 200° arc, from 190° to −10°. Every tenth tick is longer. Ticks up to the current one are drawn in the accent colour, the rest in the track colour, and the current tick is the longest and thickest. The lit fraction is `wall_w / peak_w`, clamped to 0–1, so the gauge and the usage label share the same peak. The arc is rendered with Pillow at four times the size and scaled down, and redrawn only when the lit tick or the palette changes.

### Scene

The scene is a glass dome over a small tree with an energy crystal at its base. It is drawn at a native 128 × 160 px from three indexed PNG layers in `power_meter/data/scene/`:

- `back.png`: the air, the base, soil, stones, trunk, ground plants and the core.
- `canopy.png`: three sway frames of the leaves side by side (384 × 160).
- `glass.png`: the dome's glass and highlights.

Index 0 is transparent and the layers share one 32-colour palette, plus a haze colour added at run time, so a state or the time of day is a palette change rather than a different picture. `paths.json` holds the routes, in native pixels, that the particles follow: roots, trunk and branches.

**States.** The state comes from `ratio = wall_w / peak_w` of the latest snapshot (`peak_w` is the `P_peak` from §4). It uses its own thresholds, not the usage-label ones.

| State | Shown when | Caption under the dome | Hover text |
|---|---|---|---|
| Waiting | No live data: warming up, restarting, or no snapshot | ○ Waiting for live data | Waiting for live readings |
| Idle | `ratio` below 0.20 | ● Idle · following live data | Resting — 62 W, 15% of this PC's maximum |
| Active | 0.20 ≤ `ratio` < 0.55 | ● Active · following live data | Working — … |
| High load | `ratio` ≥ 0.55 | ● High load · following live data | High load — 214 W, 78% of this PC's maximum |

- **Hysteresis.** A scene is left only after the ratio crosses its threshold by 0.04: High load is kept down to 0.51 and Active down to 0.16.
- **Dwell.** A change between live states shows only after the new state has held for 5 s. If the target changes again, or goes back to the current state, the timer starts over. Entering or leaving Waiting is immediate, and the first live reading picks its state directly. The scene never shows a made-up value: with no data it is grey, not idle.

**Per-state look.** The state changes the palette and the code-driven effects:

| State | Core | Leaves | Particles | Glow rings | Air haze |
|---|---|---|---|---|---|
| Waiting | grey | darkest greens | 0 | 0 | none |
| Idle | teal, dim glow | slightly darker greens | 4 | 1 | 4% |
| Active | teal, full glow | the drawn greens | 10 | 2 | 8% |
| High load | each core colour one step brighter | lighter greens | 18 | 3 | 14% |

The effects are drawn on the indexed image before it is coloured. Particles advance one path pixel per step, glow rings are dithered ellipses around the core whose pattern shifts every other step, and haze is ordered-dither (4 × 4 Bayer) pixels on the air. By day the air, glass and highlight colours switch to lighter ones.

**Scale.** Integer only: `pixel_size = max(1, min(300 × k // 128, 320 × k // 160))`, with `k` the display scaling, drawn with nearest-neighbour scaling. The dome is centred in the left column, so there are no half-pixels and no smoothing. The backdrop uses the same `pixel_size`.

**Rendering.** Each frame is composited from the backdrop behind the scene and the scaled art, then pasted into one `PhotoImage` that is updated in place, so there is no per-frame allocation. The caption and the hover text are canvas items, which keeps them on the backdrop colour.

### Animation

- **Rate.** 8 frames per second (one step every 125 ms).
- **Sway.** The canopy cycles through its frames 0, 1, 0, 2, holding each for 3 steps, so one cycle takes 1.5 s.
- **Twinkle.** In the dark theme, every 8 steps (1 s) two of the cross-shaped stars swap between bright and dim.
- **Pausing.** Stepping stops when the window is minimised and resumes when it is restored.
- **Still frame.** With the animation off the scene shows the frame for its state at step 0, still reacting to the state, and the stars stay bright. It is off when **Animate the scene** is unticked or Windows' *Animation effects* setting (`SPI_GETCLIENTAREAANIMATION`) is off; that flag is read at start-up and whenever settings change, and counts as on where it can't be read.

### Backdrop

A pixel backdrop spans the whole window at the scene's `pixel_size`, so the decoration and the dome share one pixel grid. It is drawn from a fixed seed, so it looks the same on every launch, and counts scale with the window area relative to a 380 × 281 reference.

- **Dark.** A faint diagonal dithered band, about 320 faint stars in three greys and 48 dim cream or teal ones, and 14 cross-shaped stars (a bright centre with four dim arms) that take turns twinkling.
- **Light.** A sun at the top left with a dithered halo and eight short rays, faint diagonal light bands, up to 7 clouds, 14 seeds and 3 birds.
- **Clear zones.** The widgets sit on the canvas with the background colour, so any decoration under them would show as a gap. Every decoration keeps 3 screen px (8 px for twinkling stars) away from the scene, the caption, the gauge, the hover text and every visible widget. The backdrop is drawn again whenever the layout changes: a theme change, Settings opening or closing, the gauge moving, the breakdown gaining or losing a row.

### Settings view

Settings replaces the dashboard in the right column (Cancel or Esc returns); the scene and backdrop stay.

- **Your electricity meter:** Normal / TOU (segmented button)
- **Price per unit:** a band dropdown for Normal, or Peak/Off-peak fields for TOU. Hint: *Find "units used" on your electricity bill.*
- **Theme:** Dark / Light (previews at once; Cancel restores the previous theme)
- **Animate the scene:** a checkbox, on by default, stored as `animate_scene` in `config.json`. It applies when the form is saved.
- **Advanced settings** (collapsed): other components W, monitor & extras W, PSU efficiency %, CPU TDP, GPU TDP. TDP fields are disabled and labelled "measured — not used" when a measured source is active. Also shows the data folder in use and, at the bottom of the form, the app version.
- **Cancel** / **Save**. Save is disabled while any field is invalid. Invalid fields get an error-coloured border and a one-line reason below.

The form scrolls inside the window.

### States

| State | Behaviour |
|---|---|
| First launch | Defaults; meter starts immediately |
| Warming up (first sample) | Values show "—"; the scene is in its waiting state; no fake numbers |
| No measured sources | Breakdown lists "estimated" for each part; status line reads "Estimates only on this PC" |
| Source demoted mid-session | Label switches to "estimated"; no popup |
| Storage not writable anywhere, or a save failed | Non-blocking note: "Settings can't be saved on this PC"; app keeps running |
| Sampler tick failed | Note: "Measurement paused — restarting" until fresh data arrives; the scene waits |

The notes share the status line. When several apply, the highest wins: restarting, then can't save, then estimates only.

### Design tokens

The palette is Earth & amber. Every colour in the window comes from `ui/theme.py`.

| Token | Dark | Light | Used for |
|---|---|---|---|
| background | `#1A1613` | `#F4EEE4` | Window, canvas |
| surface | `#231E1A` | `#FBF7F0` | Raised controls, hover text box |
| line | `#332B25` | `#E2D8C8` | Rules, outlines |
| text | `#F0E8DC` | `#2A2119` | Values, primary text |
| text_muted | `#A3968A` | `#74675A` | Captions, labels, units |
| text_faint | `#6E635A` | `#A99C8E` | Disabled text only |
| accent | `#E8B866` | `#9A5F0E` | Lit gauge ticks, measured dots, links |
| track | `#3A312A` | `#E6DDCF` | Unlit gauge ticks, bar tracks |
| error | `#FF8A70` | `#B3261E` | Invalid fields, Reset confirmation |
| focus | `#E8B866` | `#9A5F0E` | Focus ring |

Contrast against the background and the surface: text, muted text and error are at least 4.5:1 in both themes, and the accent is at least 4.5:1 on the background (9.8:1 dark, 4.5:1 light). The focus ring is at least 3:1 on the background. `text_faint` is below 4.5:1 and is used only for disabled controls. `tests/test_theme.py` checks these ratios.

- **Type:** Segoe UI, the Windows system font, with Segoe UI Light for the two large figures. Sizes: 54 (watts), 40 (cost), 22 (baht sign), 18 (watt unit), 17 (stat values), 13 (body), 11 (captions).
- **Spacing:** 4-px grid (4 / 8 / 12 / 16 / 24), with 20 px left and 24 px right margins on the dashboard. Controls share one 8 px corner radius. No gradients, glow or emoji; the only imagery is the pixel scene.
- **Accessibility:** full Tab order, visible focus ring, Enter/Space activate buttons, Esc leaves Settings. Colour is never the only signal: measured and estimated parts differ in dot shape and in the word beside it, and the scene state is also written in the caption. Icon buttons have hover tooltips naming their action.

## 7. Persistence

Two files in the same folder:

- `config.json`: user settings, including the theme and `animate_scene`. Written when settings are saved, when the theme button is used, and when the hours stepper changes.
- `state.json`: accumulated cost, energy, and elapsed time, plus an optional `learned_other_w` (the learned `P_other`, §4). The key is left out until a value has been learned; a missing, non-numeric, or out-of-range (outside 0–150 W) value loads as "not learned" without affecting the totals. Resetting the total keeps it. Written every 30 s, right after a reset, on close, and when Windows signals a shutdown or log-off (Tk's `WM_SAVE_YOURSELF`), since the process may then end without a close event. Every write happens on the sampler thread.

**Location:** the folder containing the `.exe` (`sys.executable` when frozen, not the PyInstaller temp folder). If that folder is not writable, use `%APPDATA%\PCPowerCostMeter\`. The resolved location is shown under Advanced settings.

**Writes are atomic:** write `*.tmp`, flush and fsync, then `os.replace`.

**Corrupt or invalid file:** rename it to `*.bak`, load defaults, and continue. Each field is validated against the ranges in §4/§5; out-of-range values fall back to their default individually.

**Schema:** a top-level `"schema": 1` field for future migrations.

**Logging:** `power-meter.log` in the same folder, rotating at 256 KB × 2 files. It holds sensor availability, demotions, and errors only. No usernames, paths outside the app folder, or hardware serial numbers.

## 8. Error handling

- Every sensor `open()`/`read()` catches its own `OSError`/`ctypes` errors, logs once per distinct error, and returns `None`/`False`. Nothing is swallowed silently without a log line.
- The source chain in `power_model.py` also catches anything else a source raises, so one faulty driver demotes that source instead of stopping the meter.
- An unexpected exception in a sampler tick is logged. The sampler tries again after 5 s, and the UI shows "Measurement paused — restarting" until fresh data arrives.
- An error in a Tk callback is logged instead of printed, since the packaged app has no console.
- On close, sources are closed (`nvmlShutdown`, `ADL2_Main_Control_Destroy`, `PdhCloseQuery`) and state is saved.

## 9. Dependencies

| Package | Why | Notes |
|---|---|---|
| `customtkinter` 6.x | Themed Tk widgets with built-in dark/light modes; small footprint, and bundles cleanly with PyInstaller | Pinned to major version 6 (`>=6.0,<7`) |
| `nvidia-ml-py` | Official NVIDIA bindings for NVML | Pure Python; imported lazily so it is optional at runtime |
| `pillow` 11+ | Indexed-palette sprites, nearest-neighbour scaling and compositing for the scene, and the gauge and backdrop images | MIT-CMU licence |
| dev: `pytest`, `ruff`, `mypy`, `pyinstaller` 6.22+ | Test, lint, type-check, build | PyInstaller 6.22 supports Python 3.15 |

**Not used:**
- `GPUtil`: NVIDIA-only, unmaintained, and depends on `distutils`.
- `psutil`: PDH already provides utilisation, so it is unnecessary.

Windows and AMD APIs are called via the standard-library `ctypes`.

## 10. Build, test, release

- **Tests (pytest):** `tariff` (peak/off-peak boundaries at 09:00 and 22:00, weekends, holidays, crossing midnight); `meter` (trapezoid math, sleep-gap skip, reset); `power_model` (selection, demotion/retry, raising sources, PSU math, battery path, sanity limits); `sampler` (commands, autosave, restart, shutdown); `projection`; `storage` (atomic write, corrupt file, unwritable folder fallback, range validation); the UI's pure parts (view-model, form parsing, formatting, contrast, scene state, scene frames, backdrop); and one smoke test that drives the real window over a fake sampler, skipped when Tk has no display; and the version (package attribute equals installed metadata). Sensors are tested against fake DLL objects, so the suite runs on Linux.
- **Hardware smoke test:** `tools/sensor_check.py` plus a manual checklist run on the desktop and the laptop before each release.
- **CI (GitHub Actions, `windows-latest`, Python 3.13 and 3.15):** `ruff check .`, `mypy` (strict, configured in `pyproject.toml`), `pytest` on every push and PR.
- **Release workflow (tag `v*`):** PyInstaller `--onefile --noconsole`, UPX disabled, Windows version resource, app icon. The job fails if the tag isn't `v` plus `power_meter.__version__`, which is the only place the version is written. Uploads `PCPowerCostMeter.exe` and `SHA256SUMS.txt` to the GitHub Release.
- **Repo files:** `README.md` (features, privacy, formula, limitations, how to run, SmartScreen and antivirus guidance, MIT disclaimer), `LICENSE`, `.gitignore`, `pyproject.toml`, and this document.
- **Antivirus:** unsigned PyInstaller one-file builds can trigger false positives. Mitigations: CI-built and reproducible, no UPX, published hashes, and submission to Microsoft for analysis if flagged. Code signing is out of scope.

## 11. Open items and risks

| # | Item | Resolve by |
|---|---|---|
| 1 | ADL sensor #73 is `PMLOG_BOARD_POWER` (confirmed from `adl_defines.h`); confirm it is reported on other RDNA GPUs | Testing on other AMD cards |
| 2 | Laptop: NVML power on RTX 5070 Laptop; whether polling NVML keeps the dGPU awake (hybrid graphics) | Laptop sensor check before v1.0 |
| 3 | Laptop: battery discharge rate readable as a standard user; Energy Meter on Ryzen AI | Laptop sensor check before v1.0 |
| 4 | 2027 holidays are derived from the off-peak rule and the Bank of Thailand 2027 holiday notice; check them against the PEA/MEA 2027 off-peak calendar once it is published. The ERC can also reclassify individual days (as it did for the postponed Songkran in 2020) | Before v1.0 and each January |
| 5 | AMD RAPL package power is self-reported by the CPU and may differ from external measurement | Documented limitation |
| 6 | CI runner support for Python 3.15: `actions/setup-python` provides 3.15.0, so CI tests 3.13 and 3.15 | First CI run |
