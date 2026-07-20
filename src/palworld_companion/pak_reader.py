from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


class PakReadError(RuntimeError):
    pass


class _OpenOodle:
    """Small adapter from pyuepak's decompressor API to the GPL pyooz binding."""

    __palplus_open_oodle__ = True

    def __init__(self, decompress) -> None:
        self._decompress = decompress

    def decompress(self, data: bytes, output_size: int) -> bytes:
        return self._decompress(data, output_size)


def _load_pak_file_class():
    """Load pyuepak only after replacing its network-capable Oodle helper.

    pyuepak imports ``pyuepak.oodle`` at module load and that helper may download
    a proprietary DLL. PalPlus never imports that implementation. Instead, this
    injects a process-local module backed by the open-source pyooz binding before
    pyuepak is allowed to import.
    """
    existing_oodle = sys.modules.get("pyuepak.oodle")
    if existing_oodle is not None and not getattr(existing_oodle, "__palplus_open_oodle__", False):
        raise PakReadError(
            "A non-PalPlus pyuepak Oodle helper was already loaded; refusing to use an unaudited decompressor."
        )

    existing_entry = sys.modules.get("pyuepak.entry")
    if existing_entry is not None and not getattr(
        getattr(existing_entry, "oodle_comp", None), "__palplus_open_oodle__", False
    ):
        raise PakReadError(
            "pyuepak was already initialized with an unaudited decompressor; restart PalPlus and try again."
        )

    if existing_oodle is None:
        try:
            ooz = importlib.import_module("ooz")
        except ImportError as error:
            raise PakReadError("The audited pyooz decompressor is not installed.") from error
        adapter = types.ModuleType("pyuepak.oodle")
        adapter.__palplus_open_oodle__ = True
        adapter.oodle = lambda: _OpenOodle(ooz.decompress)
        sys.modules["pyuepak.oodle"] = adapter

    try:
        pyuepak = importlib.import_module("pyuepak")
    except Exception as error:
        raise PakReadError(f"The audited pyuepak reader could not load: {error}") from error
    return pyuepak.PakFile


def read_pak_files(pak_path: Path, internal_paths: tuple[str, ...]) -> dict[str, bytes]:
    """Read an explicit allow-list of files from an Unreal pak archive."""
    if not pak_path.is_file():
        raise PakReadError(f"Palworld archive was not found: {pak_path}")
    if not internal_paths:
        return {}
    try:
        pak_file_class = _load_pak_file_class()
        archive = pak_file_class()
        archive.read(pak_path)
        return {path: archive.read_file(path) for path in internal_paths}
    except PakReadError:
        raise
    except KeyError as error:
        raise PakReadError(f"The installed Palworld archive is missing the audited map asset: {error}") from error
    except Exception as error:
        raise PakReadError(f"Could not read the installed Palworld archive: {error}") from error
