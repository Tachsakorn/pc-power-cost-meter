# Build from the repository root after `pip install -e ".[dev]"`:
#     pyinstaller packaging\power_meter.spec

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

# Fails fast if the package isn't installed; its data files could not be collected otherwise.
import power_meter  # noqa: F401

here = Path(SPECPATH)
icon = here.parent / "src" / "power_meter" / "data" / "icon.ico"

a = Analysis(
    [str(here / "launch.py")],
    datas=collect_data_files("power_meter") + collect_data_files("customtkinter"),
    hiddenimports=["pynvml"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PCPowerCostMeter",
    console=False,
    upx=False,
    icon=str(icon),
    version=str(here / "version_info.txt"),
)
