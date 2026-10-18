# PC Power & Cost Meter: Design Spec

- **Date:** 2026-10-18
- **Status:** Approved
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

## 2. Hardware evidence (probe results)

A throwaway probe (`tools/probe.py`) was run on the owner's desktop: Ryzen 5 5600, RX 7600, B450M, Windows 11 25H2, Python 3.15.0, standard user.

| Source | Result | Idle | Load |
|---|---|---|---|
| Windows *Energy Meter* PDH counter `rapl_package0_pkg` (CPU package power, reported in mW) | Works without admin | ~39 W | ~76.6 W all-core |
| AMD ADL `ADL2_New_QueryPMLogData_Get` (OverDrive 8) | Works, ~1.4 ms per call | sensor #73 ≈ 6–7 | sensor #73 ≈ 129–144 in-game |
| PDH `% Processor Utility` / `% Processor Performance` | Works | 2–6 % | 106 % (frequency-scaled) |
| PDH `GPU Engine(*engtype_3D)` utilization | Works | 0 % | tracks game load |
| NVML | Not present on desktop | n/a | n/a |
| Battery IOCTL | No battery on desktop | n/a | n/a |

**Sensor #73 has not been confirmed as a documented name.** It was identified because it tracks GPU activity and matches RX 7600's ~165 W board power. Its official enum name must be confirmed from the ADL SDK header during implementation.

**Not yet verified:** the laptop (Gigabyte, Ryzen AI, RTX 5070 Laptop). Untested there: NVML on a hybrid-graphics laptop, the battery discharge rate, and Energy Meter on Ryzen AI. The design keeps a fallback for each of these, and they must be verified before v1.0 ships (see §11).

## 3. Architecture

Layers, each with a single job. Only `sensors/` and `ui/` touch Windows or Tk; everything else is pure Python and unit-testable on any OS.

```
src/power_meter/
  __main__.py          entry point; wires everything together
  sensors/
    base.py            Reading dataclass + PowerSource protocol
    pdh.py             thin ctypes wrapper over pdh.dll (English counter names)
    energy_meter.py    CPU package watts (RAPL via Windows Energy Meter)
    amd_adl.py         AMD GPU watts (ADL PMLog)
    nvidia_nvml.py     NVIDIA GPU watts (nvidia-ml-py)
    battery.py         laptop discharge rate (SetupAPI + IOCTL_BATTERY_*)
    estimators.py      utilisation-based fallbacks for CPU and GPU
  power_model.py       picks the best source per component, returns a PowerSnapshot
  tariff.py            Standard and TOU pricing, holiday calendar
  meter.py             integrates watts into units (kWh) and Baht
  projection.py        monthly cost estimate
  settings.py          Settings dataclass, defaults, validation ranges
  storage.py           config.json / state.json load + atomic save + location fallback
  sampler.py           background thread: sample → model → meter → queue
  ui/
    theme.py           design tokens (colours, type scale, spacing)
    main_view.py
    settings_view.py
    widgets.py         small reusable pieces (usage bar, labelled field)
data/holidays_th.json
tests/
tools/probe.py         hardware diagnostic (kept as a developer tool)
```

### Data flow

```
sampler thread (every 1.0 s, monotonic clock)
  sources ──► power_model ──► PowerSnapshot(wall_w, parts[], on_battery)
                                   │
                                   ▼
            meter.add(snapshot, dt, wall_clock_now) ── uses tariff.rate_at(now)
                                   │
                                   ▼
                       queue.put(MeterState)  (latest only)

Tk main loop (after() every 250 ms)
  queue.get_nowait() ──► views render; no calculation in the UI
```

- Hardware calls never run on the Tk thread, so a slow or hung driver call cannot freeze the window.
- The UI reads only immutable `MeterState` objects. Settings changes go UI → sampler through a thread-safe `apply_settings()` call.

## 4. Power model

### Source interface

```python
@dataclass(frozen=True)
class Reading:
    watts: float
    source: str      # e.g. "Energy Meter (RAPL)", "AMD ADL", "Estimate"
    measured: bool

class PowerSource(Protocol):
    component: Literal["cpu", "gpu", "system"]
    def open(self) -> bool: ...          # False if unavailable on this PC
    def read(self) -> Reading | None: ...  # None on a transient failure
    def close(self) -> None: ...
```

### Selection order (chosen at startup; re-evaluated on failure)

| Component | 1st choice (measured) | Fallback (estimated) |
|---|---|---|
| CPU | Energy Meter `*_pkg` instances, summed (mW → W) | `cpu_tdp × (0.30 + 0.70 × util)` |
| GPU | ADL (AMD) or NVML (NVIDIA); sum all discrete GPUs | `gpu_tdp × (0.10 + 0.90 × util_3d)` of the busiest discrete adapter |
| Whole system (laptop on battery only) | battery discharge rate | n/a; use the component sum |

- `util` is PDH `% Processor Utility / 100`, clamped to 0–1. `util_3d` is the summed 3D engine utilisation per adapter LUID, clamped to 0–1.
- A source that fails 3 reads in a row is demoted to its fallback. Every 60 s, demoted sources are retried.
- Sanity limits: readings below 0 W or above 1000 W are discarded as a failed read.
- Integrated GPUs with no measured source are treated as part of the CPU package and are not estimated separately, to avoid double counting.
- The estimate uses the busiest discrete adapter only. With several discrete GPUs and no measured GPU source, it reads low.

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

| Parameter | Desktop default | Laptop default | Range |
|---|---|---|---|
| `P_other` (board, RAM, storage, fans) | 25 W | 10 W | 0–150 W |
| `η_psu` (PSU / adapter efficiency) | 87 % | 90 % | 50–99 % |
| `η_charge` | n/a | 90 % | 50–99 % |
| `P_extra` (monitor, speakers: user-added) | 0 W | 0 W | 0–500 W |
| `cpu_tdp` (fallback only) | 65 W | 45 W | 5–400 W |
| `gpu_tdp` (fallback only) | 150 W | 100 W | 0–600 W |

The desktop/laptop profile is chosen by whether a battery device exists. Every value can be changed under Advanced settings.

### Usage level (plain-language label)

`ratio = P_wall / P_peak`, where `P_peak = max(observed max this session, (cpu_tdp + gpu_tdp + P_other) / η_psu + P_extra)`.

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

All rates are all-in Baht per unit: `(energy charge + Ft) × 1.07 VAT`. Defaults use the tariff effective from the September 2026 bill (Ft = +0.1623 THB/unit) and must be confirmed against MEA/PEA before release. The fixed monthly service charge is excluded, because it does not change with PC usage.

**Standard (progressive) meter.** The app cannot know total household usage, so the user chooses their household band from the bill. The PC is priced at the household's **marginal** tier: switching the PC off removes units from the top tier the house reaches.

| Option | Rate |
|---|---|
| I don't know (default) | 4.50 |
| Up to 150 units/month | 3.38 |
| 151–400 units/month | 4.62 |
| Over 400 units/month | 4.84 |
| Custom | user value, 0.01–100 |

Known limitation: if the PC's own usage pushes the house across a tier boundary, part of it is priced at the wrong tier. This is documented in the README.

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

Goals: understandable by non-technical users, with technical detail available but hidden by default.

### Main view

```
Power & Cost Meter                        [theme] [settings]

You've spent
฿ 1.2847                                  ← primary: largest, accent green
in 2h 15m

Right now
247 W · about ฿1.11 per hour              ← secondary: accent blue
[usage bar]  Gaming-level use

If you use your PC like this [5 ▴▾] hours a day
≈ ฿166 per month

▸ Show details                            ← collapsed by default
    CPU      50 W  measured
    GPU     140 W  measured
    Other    25 W  estimated
    PSU loss 32 W  87% efficiency

Price: Normal meter · ฿4.50 per unit · Change
                                          [Reset]
```

- With TOU selected, the price line reads: `TOU · Peak ฿6.38 now · Off-peak from 22:00`.
- The total cost shows 4 decimal places. Watts are smoothed over the last 3 samples for display only; billing uses raw samples.
- **Reset** asks for confirmation by changing to "Click again to reset" for 3 s. No modal dialog.
- Window: fixed width 460 px. Height is set during UI build (target ≤ 560 px) to fit the content without scrolling.

### Settings view (replaces the main view; Back returns)

- **Your electricity meter:** Normal / TOU (segmented button)
- **Price per unit:** a band dropdown for Normal, or Peak/Off-peak fields for TOU. Hint: *Find "units used" on your electricity bill.*
- **Advanced settings** (collapsed): other components W, monitor & extras W, PSU efficiency %, CPU TDP, GPU TDP. TDP fields are disabled and labelled "measured — not used" when a measured source is active.
- **Theme:** Dark / Light (applies instantly)
- **Cancel** / **Save**. Save is disabled while any field is invalid. Invalid fields get a red border and a one-line reason below.

### States

| State | Behaviour |
|---|---|
| First launch | Defaults; meter starts immediately |
| Warming up (first sample) | Values show "—"; no fake numbers |
| No measured sources | Details list "estimated" for each part; one-line note: "Estimates only on this PC" |
| Source demoted mid-session | Label switches to "estimated"; no popup |
| Storage not writable anywhere | Non-blocking note: "Settings can't be saved on this PC"; app keeps running |

### Design tokens

| Token | Dark | Light |
|---|---|---|
| background | `#1A1B26` | `#F5F6FA` |
| surface | `#22243A` | `#FFFFFF` |
| text primary | `#E6E7EE` | `#1A1B26` |
| text secondary | `#9A9CB0` | `#5A5D72` |
| accent cost | `#2ECC71` (8.1:1) | `#1E8449` (4.4:1) |
| accent power | `#3498DB` (5.4:1) | `#1F6FA8` (5.0:1) |
| error | `#FF6B6B` | `#C0392B` |

The light-mode accents were darkened from the brief's `#27AE60`/`#2980B9`, which measured 2.7:1 and 4.0:1 against the background. Final surface and text values are confirmed with a contrast check during the UI build.

- **Type:** one family (Segoe UI, the Windows system font). Scale: 40 (cost), 22 (watts), 13 (body), 11 (captions). The cost uses tabular figures where available, so digits don't jitter.
- **Spacing:** 4-px grid (8 / 12 / 16 / 24). One corner radius (8 px). No gradients, glow, or emoji.
- **Accessibility:** full Tab order, visible focus ring, Enter/Space activate buttons, Esc leaves Settings. Colour is never the only signal; the "measured/estimated" labels are text.

## 7. Persistence

Two files in the same folder:

- `config.json`: user settings, written only on Save.
- `state.json`: accumulated cost, energy, and elapsed time. Written every 30 s and on close.

**Location:** the folder containing the `.exe` (`sys.executable` when frozen, not the PyInstaller temp folder). If that folder is not writable, use `%APPDATA%\PCPowerCostMeter\`. The resolved location is shown under Advanced settings.

**Writes are atomic:** write `*.tmp`, flush and fsync, then `os.replace`.

**Corrupt or invalid file:** rename it to `*.bak`, load defaults, and continue. Each field is validated against the ranges in §4/§5; out-of-range values fall back to their default individually.

**Schema:** a top-level `"schema": 1` field for future migrations.

**Logging:** `power-meter.log` in the same folder, rotating at 256 KB × 2 files. It holds sensor availability, demotions, and errors only. No usernames, paths outside the app folder, or hardware serial numbers.

## 8. Error handling

- Every sensor `open()`/`read()` catches its own `OSError`/`ctypes` errors, logs once per distinct error, and returns `None`/`False`. Nothing is swallowed silently without a log line.
- An unexpected exception in the sampler thread is logged. The sampler restarts after 5 s, and the UI shows "Measurement paused — restarting" until fresh data arrives.
- On close, sources are closed (`nvmlShutdown`, `ADL2_Main_Control_Destroy`, `PdhCloseQuery`) and state is saved.

## 9. Dependencies

| Package | Why | Notes |
|---|---|---|
| `customtkinter` 6.0.x | UI toolkit required by the brief | Major version 6; confirm API differences from 5.x before building UI |
| `nvidia-ml-py` | Official NVIDIA bindings for NVML | Pure Python; imported lazily so it is optional at runtime |
| dev: `pytest`, `ruff`, `mypy`, `pyinstaller` 6.22+ | Test, lint, type-check, build | PyInstaller 6.22 supports Python 3.15 |

**Not used:**
- `GPUtil`: NVIDIA-only, unmaintained, and depends on `distutils`.
- `psutil`: PDH already provides utilisation, so it is unnecessary.

Windows and AMD APIs are called via the standard-library `ctypes`.

## 10. Build, test, release

- **Tests (pytest):** `tariff` (peak/off-peak boundaries at 09:00 and 22:00, weekends, holidays, crossing midnight); `meter` (trapezoid math, sleep-gap skip, reset); `power_model` (selection, demotion/retry, PSU math, battery path, sanity limits); `projection`; `storage` (atomic write, corrupt file, unwritable folder fallback, range validation). Sensors are tested with fake sources.
- **Hardware smoke test:** `tools/probe.py` plus a manual checklist run on the desktop and the laptop before each release.
- **CI (GitHub Actions, `windows-latest`):** `ruff check`, `mypy --strict src`, `pytest` on every push and PR.
- **Release workflow (tag `v*`):** PyInstaller `--onefile --noconsole`, UPX disabled, Windows version resource, app icon. Uploads `PCPowerCostMeter.exe` and `SHA256SUMS.txt` to the GitHub Release.
- **Repo files:** `README.md` (features, privacy, formula, limitations, how to run, Defender false-positive guidance, MIT disclaimer), `LICENSE`, `.gitignore`, `pyproject.toml`, screenshots (dark and light), and this spec.
- **Antivirus:** unsigned PyInstaller one-file builds can trigger false positives. Mitigations: CI-built and reproducible, no UPX, published hashes, and submission to Microsoft for analysis if flagged. Code signing is out of scope.

## 11. Open items and risks

| # | Item | Resolve by |
|---|---|---|
| 1 | Confirm ADL sensor #73's documented name; confirm the index is stable on other RDNA GPUs | Implementation (ADL SDK header) |
| 2 | Laptop: NVML power on RTX 5070 Laptop; whether polling NVML keeps the dGPU awake (hybrid graphics) | Laptop probe before v1.0 |
| 3 | Laptop: battery discharge rate readable as a standard user; Energy Meter on Ryzen AI | Laptop probe before v1.0 |
| 4 | 2027 holidays are derived from the off-peak rule and the Bank of Thailand 2027 holiday notice; check them against the PEA/MEA 2027 off-peak calendar once it is published. The ERC can also reclassify individual days (as it did for the postponed Songkran in 2020) | Before v1.0 and each January |
| 5 | AMD RAPL package power is self-reported by the CPU and may differ from external measurement | Documented limitation |
| 6 | CI runner support for Python 3.15; fall back to 3.14 in CI if needed | First CI run |
