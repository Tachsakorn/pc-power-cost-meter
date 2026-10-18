# Build from the repository root after `pip install -e ".[dev]"`:
#     pyinstaller packaging\power_meter.spec

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

# Fails fast if the package isn't installed; its data files could not be collected otherwise.
import power_meter

here = Path(SPECPATH)
icon = here.parent / "src" / "power_meter" / "data" / "icon.ico"

version = tuple(int(part) for part in power_meter.__version__.split(".")) + (0,)
dotted = ".".join(map(str, version))
strings = {
    "CompanyName": "Tachsakorn",
    "FileDescription": "PC Power & Cost Meter",
    "FileVersion": dotted,
    "InternalName": "PCPowerCostMeter",
    "LegalCopyright": "© 2026 Tachsakorn. MIT License.",
    "OriginalFilename": "PCPowerCostMeter.exe",
    "ProductName": "PC Power & Cost Meter",
    "ProductVersion": dotted,
}
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=version,
        prodvers=version,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([StringTable("040904B0", [StringStruct(k, v) for k, v in strings.items()])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

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
    version=version_info,
)
