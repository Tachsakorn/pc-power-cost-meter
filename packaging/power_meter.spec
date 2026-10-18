# Build from the repository root after `pip install .`:
#     pyinstaller packaging/power_meter.spec
# The package must be installed so that its data files (holiday list, icon) can be collected.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

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
