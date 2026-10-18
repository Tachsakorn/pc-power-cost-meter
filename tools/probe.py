"""
Hardware capability probe for PC Power & Cost Meter.

THROWAWAY DIAGNOSTIC - not part of the app.
Read-only, standard user (no admin), no network, Python standard library only.

Usage:
    python probe.py idle     (run with the PC idle)
    python probe.py load     (run while a game or heavy workload is running)

Writes probe_<COMPUTERNAME>_<label>.txt next to this script.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

SAMPLES = 5
INTERVAL_S = 1.0

_lines: list[str] = []


def out(text: str = "") -> None:
    print(text)
    _lines.append(text)


def section(title: str) -> None:
    out()
    out("=" * 70)
    out(title)
    out("=" * 70)


def run_powershell(script: str, timeout: int = 60) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0 and not completed.stdout.strip():
        raise RuntimeError(completed.stderr.strip() or f"exit code {completed.returncode}")
    return completed.stdout.strip()


# ---------------------------------------------------------------------------
# 1. System inventory (CIM, standard user)
# ---------------------------------------------------------------------------

def probe_inventory() -> None:
    section("1. SYSTEM INVENTORY")
    out(f"Python   : {sys.version.split()[0]} ({platform.architecture()[0]})")
    out(f"Windows  : {platform.platform()}")
    script = (
        "$r = [ordered]@{"
        " cpu = @(Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,"
        "NumberOfLogicalProcessors,MaxClockSpeed);"
        " gpu = @(Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion);"
        " battery = @(Get-CimInstance Win32_Battery | Select-Object Name,DesignVoltage);"
        " board = @(Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product)"
        "}; $r | ConvertTo-Json -Depth 4 -Compress"
    )
    try:
        data = json.loads(run_powershell(script))
        for key in ("cpu", "gpu", "battery", "board"):
            items = data.get(key) or []
            if not items:
                out(f"{key:<9}: (none)")
            for item in items:
                out(f"{key:<9}: {item}")
    except Exception as exc:  # diagnostic: report every failure
        out(f"inventory FAILED: {exc!r}")


# ---------------------------------------------------------------------------
# 2. Windows performance counters (PDH via Get-Counter)
# ---------------------------------------------------------------------------

def probe_counters() -> None:
    section("2. WINDOWS PERFORMANCE COUNTERS (CPU util/freq, GPU engine util)")
    counters = [
        r"\Processor Information(_Total)\% Processor Utility",
        r"\Processor Information(_Total)\% Processor Performance",
        r"\Processor Information(_Total)\Processor Frequency",
        r"\GPU Engine(*engtype_3D)\Utilization Percentage",
    ]
    ps_list = ",".join(f"'{c}'" for c in counters)
    script = (
        f"$c = @({ps_list}); "
        f"$s = Get-Counter -Counter $c -SampleInterval {int(INTERVAL_S)} -MaxSamples {SAMPLES} "
        "-ErrorAction SilentlyContinue; "
        "$s | ForEach-Object { $t = $_.Timestamp.ToString('HH:mm:ss'); "
        "$_.CounterSamples | ForEach-Object { [pscustomobject]@{t=$t; p=$_.Path; v=$_.CookedValue} } } "
        "| ConvertTo-Json -Compress"
    )
    try:
        raw = run_powershell(script, timeout=SAMPLES * 10 + 30)
        if not raw:
            out("No counter data returned (counter names may be localized - Windows display language?)")
            return
        rows = json.loads(raw)
        if isinstance(rows, dict):
            rows = [rows]
        by_time: dict[str, dict[str, float]] = {}
        for row in rows:
            path = row["p"].lower()
            bucket = by_time.setdefault(row["t"], {})
            if "gpu engine" in path:
                luid = path.split("luid_")[1].split("_phys")[0] if "luid_" in path else "?"
                key = f"gpu3d[{luid}]"
                bucket[key] = bucket.get(key, 0.0) + float(row["v"])
            else:
                bucket[path.rsplit("\\", 1)[-1]] = float(row["v"])
        for t, values in by_time.items():
            parts = ", ".join(f"{k}={v:.1f}" for k, v in sorted(values.items()))
            out(f"{t}  {parts}")
        out("Note: '% processor performance' x base clock ~= effective clock (what Task Manager shows).")
    except Exception as exc:
        out(f"counters FAILED: {exc!r}")

    out()
    out("Energy/Power meter counter sets (real power telemetry on some devices):")
    try:
        listing = run_powershell(
            "Get-Counter -ListSet 'Energy Meter','Power Meter' -ErrorAction SilentlyContinue "
            "| Select-Object CounterSetName,Paths | ConvertTo-Json -Compress"
        )
        out(listing or "(not present on this machine)")
    except Exception as exc:
        out(f"listset FAILED: {exc!r}")

    out()
    out("Energy/Power meter instances and values:")
    try:
        values = run_powershell(
            r"Get-Counter -Counter '\Energy Meter(*)\Power','\Power Meter(*)\Power' "
            "-SampleInterval 1 -MaxSamples 3 -ErrorAction SilentlyContinue "
            "| ForEach-Object { $_.CounterSamples | ForEach-Object { '{0} = {1}' -f $_.Path, $_.CookedValue } }",
            timeout=40,
        )
        out(values or "(counter sets exist but have no instances - no metered rails on this machine)")
    except Exception as exc:
        out(f"meter read FAILED: {exc!r}")


# ---------------------------------------------------------------------------
# 3. NVIDIA NVML (direct ctypes, no pip package)
# ---------------------------------------------------------------------------

class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


def _load_nvml() -> ctypes.CDLL | None:
    candidates = [
        "nvml.dll",
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "NVIDIA Corporation", "NVSMI", "nvml.dll"),
    ]
    for candidate in candidates:
        try:
            return ctypes.CDLL(candidate)
        except OSError:
            continue
    return None


def probe_nvml() -> None:
    section("3. NVIDIA NVML (measured GPU power)")
    nvml = _load_nvml()
    if nvml is None:
        out("nvml.dll not found -> no NVIDIA driver on this machine")
        return
    nvml.nvmlErrorString.restype = ctypes.c_char_p

    def err(code: int) -> str:
        return nvml.nvmlErrorString(code).decode(errors="replace")

    started = time.perf_counter()
    rc = nvml.nvmlInit_v2()
    out(f"nvmlInit_v2: rc={rc} ({err(rc)}) in {(time.perf_counter() - started) * 1000:.0f} ms"
        "  [slow init can mean the dGPU was asleep and got woken]")
    if rc != 0:
        return
    try:
        count = ctypes.c_uint()
        nvml.nvmlDeviceGetCount_v2(ctypes.byref(count))
        out(f"device count: {count.value}")
        for index in range(count.value):
            handle = ctypes.c_void_p()
            nvml.nvmlDeviceGetHandleByIndex_v2(index, ctypes.byref(handle))
            name = ctypes.create_string_buffer(96)
            nvml.nvmlDeviceGetName(handle, name, 96)
            limit = ctypes.c_uint()
            rc_limit = nvml.nvmlDeviceGetPowerManagementLimit(handle, ctypes.byref(limit))
            out(f"[{index}] {name.value.decode(errors='replace')}  power limit: "
                + (f"{limit.value / 1000:.1f} W" if rc_limit == 0 else f"n/a ({err(rc_limit)})"))
            for _ in range(SAMPLES):
                power = ctypes.c_uint()
                util = _NvmlUtilization()
                t0 = time.perf_counter()
                rc_power = nvml.nvmlDeviceGetPowerUsage(handle, ctypes.byref(power))
                call_ms = (time.perf_counter() - t0) * 1000
                rc_util = nvml.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util))
                power_txt = f"{power.value / 1000:.1f} W" if rc_power == 0 else f"n/a ({err(rc_power)})"
                util_txt = f"{util.gpu}%" if rc_util == 0 else f"n/a ({err(rc_util)})"
                out(f"    power={power_txt}  util={util_txt}  call={call_ms:.1f} ms")
                time.sleep(INTERVAL_S)
    finally:
        nvml.nvmlShutdown()


# ---------------------------------------------------------------------------
# 4. AMD ADL (driver DLL, ctypes)
# ---------------------------------------------------------------------------

class _AdapterInfo(ctypes.Structure):
    _fields_ = [
        ("iSize", ctypes.c_int),
        ("iAdapterIndex", ctypes.c_int),
        ("strUDID", ctypes.c_char * 256),
        ("iBusNumber", ctypes.c_int),
        ("iDeviceNumber", ctypes.c_int),
        ("iFunctionNumber", ctypes.c_int),
        ("iVendorID", ctypes.c_int),
        ("strAdapterName", ctypes.c_char * 256),
        ("strDisplayName", ctypes.c_char * 256),
        ("iPresent", ctypes.c_int),
        ("iExist", ctypes.c_int),
        ("strDriverPath", ctypes.c_char * 256),
        ("strDriverPathExt", ctypes.c_char * 256),
        ("strPNPString", ctypes.c_char * 256),
        ("iOSDisplayIndex", ctypes.c_int),
    ]


class _SensorData(ctypes.Structure):
    _fields_ = [("supported", ctypes.c_int), ("value", ctypes.c_int)]


class _PMLogDataOutput(ctypes.Structure):
    _fields_ = [("size", ctypes.c_int), ("sensors", _SensorData * 256)]


# Labels from the ADL SDK ADLSensorType enum; unverified on this hardware.
_ADL_SENSOR_NAMES = {
    1: "GFX clock MHz", 2: "MEM clock MHz", 3: "SOC clock MHz",
    8: "temp edge C", 9: "temp mem C", 14: "fan RPM", 15: "fan %",
    16: "SOC voltage", 17: "SOC power", 18: "SOC current",
    19: "GFX activity %", 20: "MEM activity %", 21: "GFX voltage mV",
    22: "MEM voltage", 23: "ASIC power W",
}

_ADL_MALLOC = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_int)
_adl_buffers: list[ctypes.Array] = []


@_ADL_MALLOC
def _adl_malloc(size: int) -> int:
    buffer = ctypes.create_string_buffer(size)
    _adl_buffers.append(buffer)
    return ctypes.addressof(buffer)


def probe_adl(samples: int = SAMPLES) -> None:
    section("4. AMD ADL (measured GPU power)")
    try:
        adl = ctypes.CDLL("atiadlxx.dll")
    except OSError:
        out("atiadlxx.dll not found -> no AMD Radeon driver on this machine")
        return

    context = ctypes.c_void_p()
    rc = adl.ADL2_Main_Control_Create(_adl_malloc, 1, ctypes.byref(context))
    out(f"ADL2_Main_Control_Create rc={rc}")
    if rc != 0:
        return
    try:
        count = ctypes.c_int()
        adl.ADL2_Adapter_NumberOfAdapters_Get(context, ctypes.byref(count))
        if count.value <= 0:
            out("no ADL adapters")
            return
        infos = (_AdapterInfo * count.value)()
        rc = adl.ADL2_Adapter_AdapterInfo_Get(context, infos, ctypes.sizeof(infos))
        out(f"logical adapters: {count.value} (AdapterInfo_Get rc={rc})")

        for info in infos:
            out(f"    logical idx={info.iAdapterIndex} vendor={info.iVendorID} present={info.iPresent} "
                f"bus={info.iBusNumber} name={info.strAdapterName.decode(errors='replace')}")

        # ADL reports AMD's PCI vendor id as the decimal number 1002, not 0x1002.
        amd_vendor_ids = {1002, 0x1002}
        seen: set[tuple[int, int, int]] = set()
        for info in infos:
            physical = (info.iBusNumber, info.iDeviceNumber, info.iFunctionNumber)
            if info.iVendorID not in amd_vendor_ids or physical in seen:
                continue
            seen.add(physical)
            name = info.strAdapterName.decode(errors="replace")
            out(f"adapter index {info.iAdapterIndex}: {name} bus={physical}")

            supported, enabled, version = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
            rc_caps = adl.ADL2_Overdrive_Caps(context, info.iAdapterIndex, ctypes.byref(supported),
                                              ctypes.byref(enabled), ctypes.byref(version))
            out(f"    Overdrive caps rc={rc_caps} supported={supported.value} version={version.value}")

            for _ in range(samples):
                log = _PMLogDataOutput()
                log.size = ctypes.sizeof(log)
                t0 = time.perf_counter()
                rc_log = adl.ADL2_New_QueryPMLogData_Get(context, info.iAdapterIndex, ctypes.byref(log))
                call_ms = (time.perf_counter() - t0) * 1000
                if rc_log != 0:
                    out(f"    New_QueryPMLogData_Get rc={rc_log} (not supported)")
                    break
                values = [
                    f"{_ADL_SENSOR_NAMES.get(i, f'#{i}')}={log.sensors[i].value}"
                    for i in range(256) if log.sensors[i].supported
                ]
                out(f"    call={call_ms:.1f} ms  " + "; ".join(values))
                time.sleep(INTERVAL_S)
    finally:
        adl.ADL2_Main_Control_Destroy(context)


# ---------------------------------------------------------------------------
# 5. Battery discharge rate (SetupAPI + IOCTL_BATTERY_*, standard user)
# ---------------------------------------------------------------------------

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class _SpDeviceInterfaceData(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", _GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_size_t)]


class _BatteryWaitStatus(ctypes.Structure):
    _fields_ = [("BatteryTag", wt.ULONG), ("Timeout", wt.ULONG), ("PowerState", wt.ULONG),
                ("LowCapacity", wt.ULONG), ("HighCapacity", wt.ULONG)]


class _BatteryStatus(ctypes.Structure):
    _fields_ = [("PowerState", wt.ULONG), ("Capacity", wt.ULONG),
                ("Voltage", wt.ULONG), ("Rate", wt.LONG)]


class _BatteryQueryInformation(ctypes.Structure):
    _fields_ = [("BatteryTag", wt.ULONG), ("InformationLevel", ctypes.c_int), ("AtRate", wt.LONG)]


class _BatteryInformation(ctypes.Structure):
    _fields_ = [("Capabilities", wt.ULONG), ("Technology", ctypes.c_ubyte),
                ("Reserved", ctypes.c_ubyte * 3), ("Chemistry", ctypes.c_ubyte * 4),
                ("DesignedCapacity", wt.ULONG), ("FullChargedCapacity", wt.ULONG),
                ("DefaultAlert1", wt.ULONG), ("DefaultAlert2", wt.ULONG),
                ("CriticalBias", wt.ULONG), ("CycleCount", wt.ULONG)]


class _SystemPowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wt.DWORD), ("BatteryFullLifeTime", wt.DWORD)]


_GUID_DEVCLASS_BATTERY = _GUID(0x72631E54, 0x78A4, 0x11D0,
                               (ctypes.c_ubyte * 8)(0xBC, 0xF7, 0x00, 0xAA, 0x00, 0xB7, 0xB3, 0x2A))
_IOCTL_BATTERY_QUERY_TAG = 0x294040
_IOCTL_BATTERY_QUERY_INFORMATION = 0x294044
_IOCTL_BATTERY_QUERY_STATUS = 0x29404C
_BATTERY_CAPACITY_RELATIVE = 0x40000000
_BATTERY_UNKNOWN_RATE = -0x80000000


def _battery_paths() -> list[str]:
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, wt.DWORD]
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                                     wt.DWORD, ctypes.c_void_p]
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                                          wt.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

    digcf_present, digcf_deviceinterface = 0x2, 0x10
    devinfo = setupapi.SetupDiGetClassDevsW(ctypes.byref(_GUID_DEVCLASS_BATTERY), None, None,
                                            digcf_present | digcf_deviceinterface)
    if devinfo in (None, ctypes.c_void_p(-1).value):
        return []
    paths: list[str] = []
    try:
        for index in range(8):
            did = _SpDeviceInterfaceData()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(devinfo, None, ctypes.byref(_GUID_DEVCLASS_BATTERY),
                                                        index, ctypes.byref(did)):
                break
            required = wt.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(devinfo, ctypes.byref(did), None, 0,
                                                      ctypes.byref(required), None)
            buffer = ctypes.create_string_buffer(required.value)
            ctypes.cast(buffer, ctypes.POINTER(wt.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if setupapi.SetupDiGetDeviceInterfaceDetailW(devinfo, ctypes.byref(did), buffer, required,
                                                         None, None):
                paths.append(ctypes.wstring_at(ctypes.addressof(buffer) + 4))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(devinfo)
    return paths


def probe_battery() -> None:
    section("5. BATTERY DISCHARGE RATE (measured whole-system power on battery)")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                     wt.DWORD, wt.DWORD, ctypes.c_void_p]
    kernel32.DeviceIoControl.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                         ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    status = _SystemPowerStatus()
    kernel32.GetSystemPowerStatus(ctypes.byref(status))
    ac = {0: "on battery", 1: "plugged in (AC)"}.get(status.ACLineStatus, "unknown")
    out(f"AC line: {ac}, battery {status.BatteryLifePercent}%")

    try:
        paths = _battery_paths()
    except Exception as exc:
        out(f"SetupAPI FAILED: {exc!r}")
        return
    if not paths:
        out("no battery device -> desktop PC (expected)")
        return

    invalid = ctypes.c_void_p(-1).value
    for path in paths:
        handle = kernel32.CreateFileW(path, 0xC0000000, 0x3, None, 3, 0x80, None)
        if handle in (None, invalid):
            out(f"CreateFileW failed, error {ctypes.get_last_error()} (needs admin?)")
            continue
        try:
            returned = wt.DWORD()
            wait = wt.DWORD(0)
            tag = wt.ULONG(0)
            if not kernel32.DeviceIoControl(handle, _IOCTL_BATTERY_QUERY_TAG, ctypes.byref(wait),
                                            ctypes.sizeof(wait), ctypes.byref(tag), ctypes.sizeof(tag),
                                            ctypes.byref(returned), None) or tag.value == 0:
                out(f"QUERY_TAG failed, error {ctypes.get_last_error()}")
                continue

            query = _BatteryQueryInformation(BatteryTag=tag.value, InformationLevel=0, AtRate=0)
            binfo = _BatteryInformation()
            if kernel32.DeviceIoControl(handle, _IOCTL_BATTERY_QUERY_INFORMATION, ctypes.byref(query),
                                        ctypes.sizeof(query), ctypes.byref(binfo), ctypes.sizeof(binfo),
                                        ctypes.byref(returned), None):
                relative = bool(binfo.Capabilities & _BATTERY_CAPACITY_RELATIVE)
                out(f"design={binfo.DesignedCapacity} mWh full={binfo.FullChargedCapacity} mWh "
                    f"cycles={binfo.CycleCount} relative_units={relative}")

            for _ in range(SAMPLES):
                wait_status = _BatteryWaitStatus(BatteryTag=tag.value)
                bstatus = _BatteryStatus()
                if not kernel32.DeviceIoControl(handle, _IOCTL_BATTERY_QUERY_STATUS,
                                                ctypes.byref(wait_status), ctypes.sizeof(wait_status),
                                                ctypes.byref(bstatus), ctypes.sizeof(bstatus),
                                                ctypes.byref(returned), None):
                    out(f"QUERY_STATUS failed, error {ctypes.get_last_error()}")
                    break
                rate = ("unknown" if bstatus.Rate == _BATTERY_UNKNOWN_RATE
                        else f"{bstatus.Rate / 1000:.2f} W (negative = discharging)")
                out(f"    state=0x{bstatus.PowerState:x} capacity={bstatus.Capacity} mWh "
                    f"voltage={bstatus.Voltage} mV rate={rate}")
                time.sleep(INTERVAL_S)
        finally:
            kernel32.CloseHandle(handle)
    if status.ACLineStatus == 1:
        out("NOTE: plugged in -> rate is charge rate, not consumption. Re-run unplugged on the laptop.")


# ---------------------------------------------------------------------------

def _burn_cpu(stop_at: float) -> None:
    value = 0
    while time.time() < stop_at:
        value = (value * 1103515245 + 12345) & 0xFFFFFFFF


def start_cpu_stress(seconds: float) -> list:
    """Built-in all-core load so the 'cpu' run doesn't depend on an external benchmark."""
    import multiprocessing

    stop_at = time.time() + seconds
    workers = [multiprocessing.Process(target=_burn_cpu, args=(stop_at,), daemon=True)
               for _ in range(os.cpu_count() or 4)]
    for worker in workers:
        worker.start()
    return workers


def main() -> int:
    if sys.platform != "win32":
        print("This probe only runs on Windows.")
        return 1
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    host = os.environ.get("COMPUTERNAME", "pc")
    out(f"PC Power & Cost Meter - hardware probe  [{label}]  {time.strftime('%Y-%m-%d %H:%M:%S')}")

    workers = []
    if label == "gpu":
        print("Switch to your game now. Measuring starts in 20 seconds. Stay in the game for about 90 seconds.")
        for remaining in range(20, 0, -1):
            print(f"  {remaining}...", flush=True)
            time.sleep(1)
    if label == "cpu":
        out("Starting built-in all-core CPU load (stops automatically)...")
        workers = start_cpu_stress(seconds=90)
        time.sleep(5)  # let clocks and package power settle under load

    probes = [probe_inventory, probe_counters, probe_nvml, probe_adl, probe_battery]
    if label == "gpu":
        # Sample the GPU first and for longer: games often throttle once the console has focus.
        probes = [lambda: probe_adl(samples=45), probe_nvml, probe_inventory, probe_counters]
    try:
        for probe in probes:
            try:
                probe()
            except Exception as exc:
                out(f"{getattr(probe, '__name__', 'probe')} CRASHED: {exc!r}")
    finally:
        for worker in workers:
            worker.terminate()

    report = Path(__file__).with_name(f"probe_{host}_{label}.txt")
    report.write_text("\n".join(_lines), encoding="utf-8")
    out()
    out(f"Saved: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
