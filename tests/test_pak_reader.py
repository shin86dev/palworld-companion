import sys
import types

import pytest

from palworld_companion.pak_reader import PakReadError, read_pak_files


def test_reader_forces_pyuepak_through_open_oodle_adapter(monkeypatch, tmp_path):
    calls = []
    fake_ooz = types.ModuleType("ooz")
    fake_ooz.decompress = lambda data, size: calls.append((data, size)) or b"decoded"

    class FakePakFile:
        def read(self, path):
            self.path = path

        def read_file(self, path):
            return path.encode()

    fake_pyuepak = types.ModuleType("pyuepak")
    fake_pyuepak.PakFile = FakePakFile
    monkeypatch.setitem(sys.modules, "ooz", fake_ooz)
    monkeypatch.setitem(sys.modules, "pyuepak", fake_pyuepak)
    monkeypatch.delitem(sys.modules, "pyuepak.oodle", raising=False)
    monkeypatch.delitem(sys.modules, "pyuepak.entry", raising=False)
    archive = tmp_path / "Pal-Windows.pak"
    archive.write_bytes(b"pak")

    result = read_pak_files(archive, ("one", "two"))

    assert result == {"one": b"one", "two": b"two"}
    adapter_module = sys.modules["pyuepak.oodle"]
    assert adapter_module.__palplus_open_oodle__ is True
    assert adapter_module.oodle().decompress(b"compressed", 7) == b"decoded"
    assert calls == [(b"compressed", 7)]


def test_reader_refuses_an_already_loaded_unaudited_oodle_module(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pyuepak.oodle", types.ModuleType("pyuepak.oodle"))
    monkeypatch.delitem(sys.modules, "pyuepak.entry", raising=False)
    archive = tmp_path / "Pal-Windows.pak"
    archive.write_bytes(b"pak")

    with pytest.raises(PakReadError, match="non-PalPlus"):
        read_pak_files(archive, ("one",))
