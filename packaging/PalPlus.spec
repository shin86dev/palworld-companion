# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata


root = Path(SPEC).resolve().parent.parent
metadata = (
    copy_metadata("pyooz")
    + copy_metadata("pyuepak")
    + [
        (str(root / "src" / "palworld_companion" / "data"), "palworld_companion/data"),
        (str(root / "src" / "palworld_companion" / "assets"), "palworld_companion/assets"),
    ]
)
hidden_imports = [
    "ooz",
    "pyuepak",
    "pyuepak.entry",
    "pyuepak.file_io",
    "pyuepak.footer",
    "pyuepak.index",
    "pyuepak.pak",
    "pyuepak.utils",
    "pyuepak.version",
]

analysis = Analysis(
    [str(root / "packaging" / "palplus_entry.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=metadata,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pyuepak.cli", "pyuepak.oodle", "tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PalPlus",
    icon=str(root / "assets" / "palplus.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PalPlus",
)
