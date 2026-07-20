"""Strictly read-only Palworld process discovery and build diagnostics.

This module deliberately exposes a very small Win32 capability surface. It can
query a process, discover its main module, and read bytes. It never requests
write, operation, thread, debug, or all-access permissions.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import re
import struct
import sys
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Protocol

from .runtime_audit import (
    discover_gworld_candidates,
    discover_inline_fname_tables,
    read_local_profile,
    write_local_profile,
)


PALWORLD_PROCESS_NAME = "Palworld-Win64-Shipping.exe"

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
READ_ONLY_PROCESS_ACCESS = PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION

# Named only so tests can enforce that they never enter the access mask.
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020
PROCESS_CREATE_THREAD = 0x0002

TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
MAX_PATH = 260
MAX_MODULE_NAME32 = 255
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    base_address: int
    size: int
    path: str


@dataclass(frozen=True)
class BuildProfile:
    profile_id: str
    sha256: str
    description: str
    offsets: dict[str, int] | None = None


# Add a fingerprint only after its complete player-position pointer profile has
# been independently verified. Unknown fingerprints still fail closed.
_JULY_10_STEAM_SHA256 = "5A0009A2D429CF7B84FF22FD99B318FF7E512A91F40F463C4A7476DB9C066755"
_CURRENT_STEAM_SHA256 = "2FF94A03BC777661BE100249B4940242F70661D890C6B8F8ACA4D6DCE79EE5A5"
KNOWN_BUILD_PROFILES: dict[str, BuildProfile] = {
    _JULY_10_STEAM_SHA256: BuildProfile(
        profile_id="steam-1.0-2026-07-10",
        sha256=_JULY_10_STEAM_SHA256,
        description="Palworld 1.0 Steam executable dated 2026-07-10",
        offsets={
            "gworld_rva": 0x965AB80,
            "world_game_instance": 0x1B8,
            "game_instance_local_players": 0x38,
            "local_player_controller": 0x30,
            "player_controller_pawn": 0x338,
            "actor_root_component": 0x198,
            "scene_component_location": 0x128,
            "scene_component_rotation": 0x140,
            "controller_player_state": 0x298,
            "player_state_record_data": 0x650,
            "record_normal_boss_defeat_array": 0x0448,
            "record_fast_travel_unlock_array": 0x2D90,
            "rep_bool_items": 0x118,
            "rep_bool_item_stride": 0x40,
            "rep_bool_item_key": 0x0C,
            "rep_bool_item_value": 0x14,
            "fname_pool_rva": 0x93D4E20,
            "fname_pool_chunks": 0x100,
            "fname_pool_chunks_indirect": 1,
        },
    ),
    _CURRENT_STEAM_SHA256: BuildProfile(
        profile_id="steam-1.0-build-24181527",
        sha256=_CURRENT_STEAM_SHA256,
        description="Palworld 1.0 Steam build 24181527 dated 2026-07-15",
        offsets={
            "gworld_rva": 0x965BBE0,
            "world_game_instance": 0x1B8,
            "game_instance_local_players": 0x38,
            "local_player_controller": 0x30,
            "player_controller_pawn": 0x338,
            "actor_root_component": 0x198,
            "scene_component_location": 0x128,
            "scene_component_rotation": 0x140,
            "controller_player_state": 0x298,
            "player_state_record_data": 0x650,
            "record_normal_boss_defeat_array": 0x0448,
            "record_fast_travel_unlock_array": 0x2D90,
            "rep_bool_items": 0x118,
            "rep_bool_item_stride": 0x40,
            "rep_bool_item_key": 0x0C,
            "rep_bool_item_value": 0x14,
            "fname_pool_rva": 0x944DB80,
            "fname_pool_chunks": 0x10,
            "fname_pool_chunks_indirect": 0,
        },
    )
}


class ReadOnlyBackend(Protocol):
    def find_process(self, executable_name: str) -> ProcessInfo | None: ...

    def open_process(self, pid: int) -> int: ...

    def executable_path(self, handle: int) -> Path: ...

    def main_module(self, pid: int) -> ModuleInfo: ...

    def read(self, handle: int, address: int, size: int) -> bytes: ...

    def close_process(self, handle: int) -> None: ...


class Win32ProbeError(RuntimeError):
    def __init__(self, operation: str, error_code: int) -> None:
        self.operation = operation
        self.error_code = error_code
        message = ctypes.FormatError(error_code).strip() if error_code else "unknown Win32 error"
        super().__init__(f"{operation} failed ({error_code}): {message}")


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(wintypes.BYTE)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * (MAX_MODULE_NAME32 + 1)),
        ("szExePath", wintypes.WCHAR * MAX_PATH),
    ]


class WindowsReadOnlyBackend:
    """Minimal Win32 backend with an intentionally non-configurable access mask."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Live process diagnostics are available on Windows only.")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        k32 = self.kernel32
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.restype = wintypes.BOOL
        k32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        k32.Module32FirstW.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.ReadProcessMemory.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.LPVOID,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        k32.ReadProcessMemory.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL

    def _snapshot(self, flags: int, pid: int = 0) -> int:
        handle = self.kernel32.CreateToolhelp32Snapshot(flags, pid)
        if handle == INVALID_HANDLE_VALUE:
            raise Win32ProbeError("CreateToolhelp32Snapshot", ctypes.get_last_error())
        return int(handle)

    def find_process(self, executable_name: str) -> ProcessInfo | None:
        snapshot = self._snapshot(TH32CS_SNAPPROCESS)
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                if entry.szExeFile.casefold() == executable_name.casefold():
                    return ProcessInfo(pid=int(entry.th32ProcessID), name=entry.szExeFile)
                ok = self.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            return None
        finally:
            self.kernel32.CloseHandle(snapshot)

    def open_process(self, pid: int) -> int:
        handle = self.kernel32.OpenProcess(READ_ONLY_PROCESS_ACCESS, False, pid)
        if not handle:
            raise Win32ProbeError("OpenProcess(read-only)", ctypes.get_last_error())
        return int(handle)

    def executable_path(self, handle: int) -> Path:
        capacity = 32768
        buffer = ctypes.create_unicode_buffer(capacity)
        size = wintypes.DWORD(capacity)
        if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise Win32ProbeError("QueryFullProcessImageNameW", ctypes.get_last_error())
        return Path(buffer.value)

    def main_module(self, pid: int) -> ModuleInfo:
        snapshot = self._snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not self.kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
                raise Win32ProbeError("Module32FirstW", ctypes.get_last_error())
            return ModuleInfo(
                name=entry.szModule,
                base_address=ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0,
                size=int(entry.modBaseSize),
                path=entry.szExePath,
            )
        finally:
            self.kernel32.CloseHandle(snapshot)

    def read(self, handle: int, address: int, size: int) -> bytes:
        if size <= 0:
            raise ValueError("Read size must be positive.")
        buffer = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t()
        ok = self.kernel32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(bytes_read),
        )
        if not ok:
            raise Win32ProbeError("ReadProcessMemory", ctypes.get_last_error())
        if bytes_read.value != size:
            raise RuntimeError(f"ReadProcessMemory returned {bytes_read.value} of {size} requested bytes.")
        return buffer.raw

    def close_process(self, handle: int) -> None:
        self.kernel32.CloseHandle(handle)


class PalworldLiveReader:
    """Long-lived, hash-gated reader for low-overhead live samples."""

    def __init__(
        self,
        backend: ReadOnlyBackend | None = None,
        profiles: dict[str, BuildProfile] | None = None,
        profile_root: Path | None = None,
        auditor: Callable[..., BuildProfile] | None = None,
        audit_observer: Callable[[str], None] | None = None,
    ) -> None:
        self.backend = backend if backend is not None else WindowsReadOnlyBackend()
        self.profiles = dict(profiles if profiles is not None else KNOWN_BUILD_PROFILES)
        self.profile_root = profile_root
        self.handle: int | None = None
        self._fname_cache: dict[int, str] = {}
        process = self.backend.find_process(PALWORLD_PROCESS_NAME)
        if process is None:
            raise RuntimeError("Palworld is not running.")
        self.process = process
        self.handle = self.backend.open_process(process.pid)
        try:
            self.module = self.backend.main_module(process.pid)
            executable_path = self.backend.executable_path(self.handle)
            fingerprint = sha256_file(executable_path)
            header = self.backend.read(self.handle, self.module.base_address, 2)
            if header != b"MZ":
                raise RuntimeError("The selected Palworld module did not have a valid PE header.")
            profile = self.profiles.get(fingerprint)
            if profile is None:
                profile = load_local_build_profile(fingerprint, profile_root)
            if profile is None:
                selected_auditor = auditor or audit_build_profile
                try:
                    if audit_observer is not None:
                        audit_observer("validating")
                    profile = selected_auditor(
                        backend=self.backend,
                        handle=self.handle,
                        module=self.module,
                        executable_path=executable_path,
                        fingerprint=fingerprint,
                        seed_profiles=self.profiles,
                        profile_root=profile_root,
                    )
                    if audit_observer is not None:
                        audit_observer("admitted-local")
                except Exception as error:
                    if audit_observer is not None:
                        audit_observer("failed")
                    raise RuntimeError(
                        "Unsupported Palworld executable fingerprint: "
                        f"{fingerprint}; local auto-audit failed: {error}"
                    ) from error
            if profile.sha256.upper() != fingerprint:
                raise RuntimeError("A local build profile did not match the executable fingerprint.")
            if profile.offsets is None:
                raise RuntimeError(f"Build profile {profile.profile_id} has no pointer offsets.")
            self.profiles[fingerprint] = profile
            self.fingerprint = fingerprint
            self.profile = profile
        except Exception:
            self.close()
            raise

    def sample(self) -> dict:
        if self.handle is None:
            raise RuntimeError("The live reader is closed.")
        sample = resolve_player_sample(self.backend, self.handle, self.module, self.profile)
        sample["waypoint_unlock_state"] = waypoint_unlock_diagnostic(
            self.backend,
            self.handle,
            self.module,
            self.profile,
            int(sample["chain"]["player_controller"], 16),
            self._fname_cache,
        )
        sample["alpha_first_clear_state"] = alpha_first_clear_diagnostic(
            self.backend,
            self.handle,
            self.module,
            self.profile,
            int(sample["chain"]["player_controller"], 16),
            self._fname_cache,
        )
        return sample

    def close(self) -> None:
        if self.handle is not None:
            self.backend.close_process(self.handle)
            self.handle = None

    def __enter__(self) -> "PalworldLiveReader":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def build_support(sha256: str, profiles: dict[str, BuildProfile]) -> dict:
    profile = profiles.get(sha256.upper())
    if profile is None:
        return {
            "supported": False,
            "profile_id": None,
            "reason": "No independently verified pointer profile matches this executable fingerprint.",
        }
    return {
        "supported": True,
        "profile_id": profile.profile_id,
        "description": profile.description,
        "offsets_complete": profile.offsets is not None,
        "reason": "Executable fingerprint matches a verified pointer profile.",
    }


def load_local_build_profile(fingerprint: str, root: Path | None = None) -> BuildProfile | None:
    payload = read_local_profile(fingerprint, root)
    if payload is None:
        return None
    raw = payload.get("profile", {})
    offsets = raw.get("offsets")
    if not isinstance(offsets, dict) or not all(
        isinstance(name, str) and isinstance(value, int) for name, value in offsets.items()
    ):
        return None
    return BuildProfile(
        profile_id=str(raw.get("profile_id", "")),
        sha256=str(raw.get("sha256", "")).upper(),
        description=str(raw.get("description", "Locally audited Palworld build")),
        offsets=offsets,
    )


def _read_pointer(backend: ReadOnlyBackend, handle: int, address: int, label: str) -> int:
    value = struct.unpack("<Q", backend.read(handle, address, 8))[0]
    if not 0x10000 <= value <= 0x7FFFFFFFFFFF:
        raise RuntimeError(f"{label} resolved to an invalid pointer: 0x{value:X}")
    return value


def _required_offsets(profile: BuildProfile, names: set[str]) -> dict[str, int]:
    if profile.offsets is None:
        raise RuntimeError(f"Build profile {profile.profile_id} has no pointer offsets.")
    missing = sorted(names - profile.offsets.keys())
    if missing:
        raise RuntimeError(f"Build profile {profile.profile_id} is missing offsets: {', '.join(missing)}")
    return profile.offsets


def _decode_fname(
    backend: ReadOnlyBackend,
    handle: int,
    module: ModuleInfo,
    offsets: dict[str, int],
    comparison_index: int,
    cache: dict[int, str] | None = None,
) -> str:
    if cache is not None and comparison_index in cache:
        return cache[comparison_index]
    chunk_index = comparison_index >> 16
    byte_offset = (comparison_index & 0xFFFF) << 1
    if not 0 <= chunk_index < 4096:
        raise RuntimeError(f"FName chunk index is out of range: {chunk_index}")
    pool = module.base_address + offsets["fname_pool_rva"]
    chunks = pool + offsets["fname_pool_chunks"]
    if offsets.get("fname_pool_chunks_indirect", 1):
        chunks = _read_pointer(
            backend,
            handle,
            chunks,
            "FNamePool chunks",
        )
    chunk = _read_pointer(
        backend,
        handle,
        chunks + (chunk_index * 8),
        f"FNamePool chunk {chunk_index}",
    )
    entry = chunk + byte_offset
    header = struct.unpack("<H", backend.read(handle, entry, 2))[0]
    length = (header >> 6) & 0x3FF
    is_wide = bool(header & 1)
    if not 1 <= length <= 1024:
        raise RuntimeError(f"FName entry has an invalid length: {length}")
    raw = backend.read(handle, entry + 2, length * (2 if is_wide else 1))
    name = raw.decode("utf-16-le" if is_wide else "utf-8")
    if cache is not None:
        cache[comparison_index] = name
    return name


def _decode_fname_value(
    backend: ReadOnlyBackend,
    handle: int,
    module: ModuleInfo,
    offsets: dict[str, int],
    raw_fname: bytes,
    cache: dict[int, str] | None = None,
) -> str:
    """Decode an eight-byte FName, including Unreal's numbered-name suffix."""
    comparison_index, number = struct.unpack("<II", raw_fname)
    base_name = _decode_fname(
        backend,
        handle,
        module,
        offsets,
        comparison_index,
        cache,
    )
    return base_name if number == 0 else f"{base_name}_{number - 1}"


def resolve_waypoint_unlock_state(
    backend: ReadOnlyBackend,
    handle: int,
    module: ModuleInfo,
    profile: BuildProfile,
    player_controller: int,
    fname_cache: dict[int, str] | None = None,
) -> dict:
    """Read the replicated fast-travel unlock set for one exact game build."""
    required = {
        "controller_player_state",
        "player_state_record_data",
        "record_fast_travel_unlock_array",
        "rep_bool_items",
        "rep_bool_item_stride",
        "rep_bool_item_key",
        "rep_bool_item_value",
        "fname_pool_rva",
        "fname_pool_chunks",
    }
    offsets = _required_offsets(profile, required)
    player_state = _read_pointer(
        backend,
        handle,
        player_controller + offsets["controller_player_state"],
        "AController.PlayerState",
    )
    record_data = _read_pointer(
        backend,
        handle,
        player_state + offsets["player_state_record_data"],
        "APalPlayerState.RecordData",
    )
    unlock_array = record_data + offsets["record_fast_travel_unlock_array"]
    items_address, item_count, item_capacity = struct.unpack(
        "<QII",
        backend.read(handle, unlock_array + offsets["rep_bool_items"], 16),
    )
    if not 0 <= item_count <= item_capacity <= 4096:
        raise RuntimeError(
            "FastTravelPointUnlockFlag has an invalid array shape: "
            f"count={item_count}, capacity={item_capacity}"
        )
    stride = offsets["rep_bool_item_stride"]
    key_offset = offsets["rep_bool_item_key"]
    value_offset = offsets["rep_bool_item_value"]
    if stride < 16 or key_offset + 4 > stride or value_offset >= stride:
        raise RuntimeError("Fast-travel unlock item layout is invalid.")
    if item_count and not 0x10000 <= items_address <= 0x7FFFFFFFFFFF:
        raise RuntimeError(f"Fast-travel unlock items resolved to an invalid pointer: 0x{items_address:X}")
    items = backend.read(handle, items_address, item_count * stride) if item_count else b""
    stable_header = backend.read(handle, unlock_array + offsets["rep_bool_items"], 16)
    if stable_header != struct.pack("<QII", items_address, item_count, item_capacity):
        raise RuntimeError("FastTravelPointUnlockFlag changed while it was being sampled.")
    unlocked_keys: set[str] = set()
    decoded_count = 0
    for index in range(item_count):
        item_offset = index * stride
        comparison_index = struct.unpack_from("<I", items, item_offset + key_offset)[0]
        key = _decode_fname(
            backend,
            handle,
            module,
            offsets,
            comparison_index,
            fname_cache,
        ).upper()
        if not re.fullmatch(r"[0-9A-F]{32}", key):
            raise RuntimeError(f"Fast-travel unlock key is not a 32-digit GUID: {key!r}")
        decoded_count += 1
        if items[item_offset + value_offset]:
            unlocked_keys.add(key)
    return {
        "status": "ready",
        "source": "UPalPlayerRecordData.FastTravelPointUnlockFlag",
        "authoritative_for_build": True,
        "record_count": item_count,
        "decoded_count": decoded_count,
        "unlocked_count": len(unlocked_keys),
        "unlocked_keys": sorted(unlocked_keys),
    }


def waypoint_unlock_diagnostic(
    backend: ReadOnlyBackend,
    handle: int,
    module: ModuleInfo,
    profile: BuildProfile,
    player_controller: int,
    fname_cache: dict[int, str] | None = None,
) -> dict:
    try:
        return resolve_waypoint_unlock_state(
            backend,
            handle,
            module,
            profile,
            player_controller,
            fname_cache,
        )
    except Exception as error:
        return {
            "status": "unavailable",
            "source": "UPalPlayerRecordData.FastTravelPointUnlockFlag",
            "error": {"type": type(error).__name__, "message": str(error)},
        }


def resolve_alpha_first_clear_state(
    backend: ReadOnlyBackend,
    handle: int,
    module: ModuleInfo,
    profile: BuildProfile,
    player_controller: int,
    fname_cache: dict[int, str] | None = None,
) -> dict:
    """Read Palworld's replicated per-field-boss first-clear flags."""
    required = {
        "controller_player_state",
        "player_state_record_data",
        "record_normal_boss_defeat_array",
        "rep_bool_items",
        "rep_bool_item_stride",
        "rep_bool_item_key",
        "rep_bool_item_value",
        "fname_pool_rva",
        "fname_pool_chunks",
    }
    offsets = _required_offsets(profile, required)
    player_state = _read_pointer(
        backend,
        handle,
        player_controller + offsets["controller_player_state"],
        "AController.PlayerState",
    )
    record_data = _read_pointer(
        backend,
        handle,
        player_state + offsets["player_state_record_data"],
        "APalPlayerState.RecordData",
    )
    clear_array = record_data + offsets["record_normal_boss_defeat_array"]
    items_address, item_count, item_capacity = struct.unpack(
        "<QII",
        backend.read(handle, clear_array + offsets["rep_bool_items"], 16),
    )
    if not 0 <= item_count <= item_capacity <= 4096:
        raise RuntimeError(
            "NormalBossDefeatFlag has an invalid array shape: "
            f"count={item_count}, capacity={item_capacity}"
        )
    stride = offsets["rep_bool_item_stride"]
    key_offset = offsets["rep_bool_item_key"]
    value_offset = offsets["rep_bool_item_value"]
    if stride < 16 or key_offset + 8 > stride or value_offset >= stride:
        raise RuntimeError("Normal-boss defeat item layout is invalid.")
    if item_count and not 0x10000 <= items_address <= 0x7FFFFFFFFFFF:
        raise RuntimeError(f"Normal-boss defeat items resolved to an invalid pointer: 0x{items_address:X}")
    items = backend.read(handle, items_address, item_count * stride) if item_count else b""
    stable_header = backend.read(handle, clear_array + offsets["rep_bool_items"], 16)
    if stable_header != struct.pack("<QII", items_address, item_count, item_capacity):
        raise RuntimeError("NormalBossDefeatFlag changed while it was being sampled.")
    cleared_keys: set[str] = set()
    for index in range(item_count):
        item_offset = index * stride
        key = _decode_fname_value(
            backend,
            handle,
            module,
            offsets,
            items[item_offset + key_offset:item_offset + key_offset + 8],
            fname_cache,
        ).upper()
        if not key or len(key) > 256:
            raise RuntimeError(f"Normal-boss defeat key is invalid: {key!r}")
        if items[item_offset + value_offset]:
            cleared_keys.add(key)
    return {
        "status": "ready",
        "source": "UPalPlayerRecordData.NormalBossDefeatFlag",
        "authoritative_for_build": True,
        "record_count": item_count,
        "cleared_count": len(cleared_keys),
        "cleared_keys": sorted(cleared_keys),
    }


def alpha_first_clear_diagnostic(
    backend: ReadOnlyBackend,
    handle: int,
    module: ModuleInfo,
    profile: BuildProfile,
    player_controller: int,
    fname_cache: dict[int, str] | None = None,
) -> dict:
    try:
        return resolve_alpha_first_clear_state(
            backend,
            handle,
            module,
            profile,
            player_controller,
            fname_cache,
        )
    except Exception as error:
        return {
            "status": "unavailable",
            "source": "UPalPlayerRecordData.NormalBossDefeatFlag",
            "error": {"type": type(error).__name__, "message": str(error)},
        }


def resolve_player_sample(
    backend: ReadOnlyBackend,
    handle: int,
    module: ModuleInfo,
    profile: BuildProfile,
) -> dict:
    """Resolve one live XYZ/rotation sample through the verified object chain."""
    if profile.offsets is None:
        raise RuntimeError(f"Build profile {profile.profile_id} has no pointer offsets.")
    offsets = profile.offsets
    required = {
        "gworld_rva",
        "world_game_instance",
        "game_instance_local_players",
        "local_player_controller",
        "player_controller_pawn",
        "actor_root_component",
        "scene_component_location",
        "scene_component_rotation",
    }
    missing = sorted(required - offsets.keys())
    if missing:
        raise RuntimeError(f"Build profile {profile.profile_id} is missing offsets: {', '.join(missing)}")

    world = _read_pointer(backend, handle, module.base_address + offsets["gworld_rva"], "GWorld")
    game_instance = _read_pointer(
        backend,
        handle,
        world + offsets["world_game_instance"],
        "UWorld.OwningGameInstance",
    )
    players_data = backend.read(
        handle,
        game_instance + offsets["game_instance_local_players"],
        16,
    )
    players_array, player_count, player_capacity = struct.unpack("<QII", players_data)
    if not 1 <= player_count <= player_capacity <= 8:
        raise RuntimeError(
            "UGameInstance.LocalPlayers has an invalid array shape: "
            f"count={player_count}, capacity={player_capacity}"
        )
    local_player = _read_pointer(backend, handle, players_array, "LocalPlayers[0]")
    player_controller = _read_pointer(
        backend,
        handle,
        local_player + offsets["local_player_controller"],
        "ULocalPlayer.PlayerController",
    )
    pawn = _read_pointer(
        backend,
        handle,
        player_controller + offsets["player_controller_pawn"],
        "APlayerController.AcknowledgedPawn",
    )
    root_component = _read_pointer(
        backend,
        handle,
        pawn + offsets["actor_root_component"],
        "AActor.RootComponent",
    )
    position = struct.unpack(
        "<ddd",
        backend.read(handle, root_component + offsets["scene_component_location"], 24),
    )
    rotation = struct.unpack(
        "<ddd",
        backend.read(handle, root_component + offsets["scene_component_rotation"], 24),
    )
    if not all(math.isfinite(value) and abs(value) < 2_000_000 for value in position):
        raise RuntimeError(f"Player position failed sanity checks: {position!r}")
    if not all(math.isfinite(value) and abs(value) <= 3600 for value in rotation):
        raise RuntimeError(f"Player rotation failed sanity checks: {rotation!r}")

    return {
        "sampled_at_utc": datetime.now(UTC).isoformat(),
        "coordinate_storage": "float64",
        "position": {"x": position[0], "y": position[1], "z": position[2]},
        "rotation": {"pitch": rotation[0], "yaw": rotation[1], "roll": rotation[2]},
        "heading_degrees": rotation[1] % 360,
        "chain": {
            "world": f"0x{world:X}",
            "game_instance": f"0x{game_instance:X}",
            "local_player": f"0x{local_player:X}",
            "player_controller": f"0x{player_controller:X}",
            "pawn": f"0x{pawn:X}",
            "root_component": f"0x{root_component:X}",
        },
    }


def _waypoint_comparison_indices(
    backend: ReadOnlyBackend,
    handle: int,
    profile: BuildProfile,
    player_controller: int,
) -> tuple[list[int], int]:
    required = {
        "controller_player_state",
        "player_state_record_data",
        "record_fast_travel_unlock_array",
        "rep_bool_items",
        "rep_bool_item_stride",
        "rep_bool_item_key",
    }
    offsets = _required_offsets(profile, required)
    player_state = _read_pointer(
        backend,
        handle,
        player_controller + offsets["controller_player_state"],
        "AController.PlayerState",
    )
    record_data = _read_pointer(
        backend,
        handle,
        player_state + offsets["player_state_record_data"],
        "APalPlayerState.RecordData",
    )
    header_address = (
        record_data
        + offsets["record_fast_travel_unlock_array"]
        + offsets["rep_bool_items"]
    )
    items_address, item_count, item_capacity = struct.unpack(
        "<QII", backend.read(handle, header_address, 16)
    )
    if not 0 <= item_count <= item_capacity <= 4096:
        raise RuntimeError(
            "FastTravelPointUnlockFlag has an invalid array shape during auto-audit: "
            f"count={item_count}, capacity={item_capacity}."
        )
    stride = offsets["rep_bool_item_stride"]
    key_offset = offsets["rep_bool_item_key"]
    if item_count and not 0x10000 <= items_address <= 0x7FFFFFFFFFFF:
        raise RuntimeError(
            f"Fast-travel unlock items resolved to an invalid pointer: 0x{items_address:X}"
        )
    items = backend.read(handle, items_address, item_count * stride) if item_count else b""
    if backend.read(handle, header_address, 16) != struct.pack(
        "<QII", items_address, item_count, item_capacity
    ):
        raise RuntimeError("FastTravelPointUnlockFlag changed during the local audit.")
    return [
        struct.unpack_from("<I", items, index * stride + key_offset)[0]
        for index in range(item_count)
    ], item_count


def _known_waypoint_keys() -> set[str]:
    path = Path(__file__).parent / "data" / "fast_travel.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(location["upstream_key"]).upper()
        for location in payload.get("locations", [])
        if location.get("upstream_key")
    }


def audit_build_profile(
    *,
    backend: ReadOnlyBackend,
    handle: int,
    module: ModuleInfo,
    executable_path: Path,
    fingerprint: str,
    seed_profiles: dict[str, BuildProfile],
    profile_root: Path | None = None,
) -> BuildProfile:
    """Discover and admit one exact local build only after strict live validation."""
    started_at = datetime.now(UTC)
    report: dict = {
        "schema_version": 1,
        "status": "failed",
        "audit": {
            "started_at_utc": started_at.isoformat(),
            "provider": "local_static_scan_plus_win32_external_readonly",
            "network_used": False,
            "write_access_requested": False,
            "fingerprint": fingerprint,
        },
    }
    try:
        usable_seeds = [profile for profile in seed_profiles.values() if profile.offsets]
        if not usable_seeds:
            raise RuntimeError("No verified seed profile is available for downstream field offsets.")
        seed = usable_seeds[-1]
        candidate_rvas = discover_gworld_candidates(executable_path)
        valid_worlds: list[tuple[int, dict]] = []
        for candidate_rva in candidate_rvas:
            offsets = dict(seed.offsets or {})
            offsets["gworld_rva"] = candidate_rva
            candidate = BuildProfile("local-audit-candidate", fingerprint, "audit candidate", offsets)
            try:
                sample = resolve_player_sample(backend, handle, module, candidate)
            except Exception:
                continue
            valid_worlds.append((candidate_rva, sample))
        if len(valid_worlds) != 1:
            raise RuntimeError(
                "GWorld discovery did not produce exactly one validated candidate: "
                f"{len(valid_worlds)} passed out of {len(candidate_rvas)}."
            )

        gworld_rva, initial_sample = valid_worlds[0]
        base_offsets = dict(seed.offsets or {})
        base_offsets["gworld_rva"] = gworld_rva
        world_profile = BuildProfile("local-audit-world", fingerprint, "audit candidate", base_offsets)
        player_controller = int(initial_sample["chain"]["player_controller"], 16)
        comparison_indices, waypoint_record_count = _waypoint_comparison_indices(
            backend, handle, world_profile, player_controller
        )
        table_candidates = discover_inline_fname_tables(
            backend,
            handle,
            module.base_address,
            executable_path,
            comparison_indices,
        )
        known_keys = _known_waypoint_keys()
        admitted: list[tuple[BuildProfile, dict, dict, dict]] = []
        for table in table_candidates:
            sampled_names = set(table["sampled_names"])
            joined_names = sampled_names & known_keys
            if sampled_names and len(joined_names) < max(1, len(sampled_names) - 3):
                continue
            offsets = dict(base_offsets)
            offsets.update(
                fname_pool_rva=int(table["table_rva"]),
                fname_pool_chunks=0,
                fname_pool_chunks_indirect=0,
            )
            candidate = BuildProfile("local-audit-candidate", fingerprint, "audit candidate", offsets)
            try:
                waypoint_state = resolve_waypoint_unlock_state(
                    backend, handle, module, candidate, player_controller
                )
                alpha_state = resolve_alpha_first_clear_state(
                    backend, handle, module, candidate, player_controller
                )
                for _ in range(3):
                    resolve_player_sample(backend, handle, module, candidate)
            except Exception:
                continue
            if waypoint_state["decoded_count"] != waypoint_record_count:
                continue
            admitted.append((candidate, table, waypoint_state, alpha_state))
        if len(admitted) != 1:
            raise RuntimeError(
                "FName discovery did not produce exactly one fully validated table: "
                f"{len(admitted)} passed out of {len(table_candidates)}."
            )

        candidate, table, waypoint_state, alpha_state = admitted[0]
        profile = BuildProfile(
            profile_id=f"local-auto-{fingerprint[:12].lower()}",
            sha256=fingerprint,
            description=(
                "Locally auto-audited Palworld executable dated "
                f"{datetime.fromtimestamp(executable_path.stat().st_mtime, UTC).date().isoformat()}"
            ),
            offsets=dict(candidate.offsets or {}),
        )
        report.update(
            status="admitted-local",
            profile=asdict(profile),
            evidence={
                "seed_profile_id": seed.profile_id,
                "gworld_static_candidate_count": len(candidate_rvas),
                "gworld_validated_candidate_count": 1,
                "gworld_rva": f"0x{gworld_rva:X}",
                "fname_table_candidate_count": len(table_candidates),
                "fname_table_rva": f"0x{int(table['table_rva']):X}",
                "fname_landmark_count": int(table["sample_count"]),
                "waypoint_record_count": waypoint_state["record_count"],
                "waypoint_decoded_count": waypoint_state["decoded_count"],
                "alpha_record_count": alpha_state["record_count"],
                "repeated_player_samples": 3,
                "map_asset_validation": "separate fail-closed map subsystem",
            },
        )
        report["audit"]["completed_at_utc"] = datetime.now(UTC).isoformat()
        write_local_profile(fingerprint, report, profile_root)
        return profile
    except Exception as error:
        report["audit"]["completed_at_utc"] = datetime.now(UTC).isoformat()
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        try:
            write_local_profile(fingerprint, report, profile_root)
        except OSError:
            pass
        raise


def probe_palworld(
    backend: ReadOnlyBackend | None = None,
    profiles: dict[str, BuildProfile] | None = None,
    *,
    auto_audit: bool = False,
    profile_root: Path | None = None,
    auditor: Callable[..., BuildProfile] | None = None,
) -> dict:
    """Return a structured, fail-closed diagnostic without changing game state."""
    result: dict = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "provider": "win32_external_readonly",
        "process_name": PALWORLD_PROCESS_NAME,
        "access": {
            "mask": f"0x{READ_ONLY_PROCESS_ACCESS:04X}",
            "requested": ["PROCESS_VM_READ", "PROCESS_QUERY_LIMITED_INFORMATION"],
            "forbidden": [
                "PROCESS_VM_WRITE",
                "PROCESS_VM_OPERATION",
                "PROCESS_CREATE_THREAD",
                "PROCESS_ALL_ACCESS",
            ],
        },
    }
    if backend is None:
        try:
            backend = WindowsReadOnlyBackend()
        except Exception as error:
            result.update(
                status="platform_unsupported",
                error={"type": type(error).__name__, "message": str(error)},
            )
            return result

    try:
        process = backend.find_process(PALWORLD_PROCESS_NAME)
        if process is None:
            result["status"] = "game_not_running"
            return result
        result["process"] = asdict(process)
        handle = backend.open_process(process.pid)
        try:
            executable_path = backend.executable_path(handle)
            module = backend.main_module(process.pid)
            header = backend.read(handle, module.base_address, 64)
            fingerprint = sha256_file(executable_path)
            stat = executable_path.stat()
            result.update(
                executable={
                    "path": str(executable_path),
                    "sha256": fingerprint,
                    "size": stat.st_size,
                    "modified_utc": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                },
                module={
                    "name": module.name,
                    "base_address": f"0x{module.base_address:X}",
                    "size": module.size,
                    "path": module.path,
                },
                memory_check={
                    "address": f"0x{module.base_address:X}",
                    "bytes_read": len(header),
                    "pe_signature": header[:2].decode("ascii", errors="replace"),
                    "valid_pe_header": header[:2] == b"MZ",
                },
            )
            selected_profiles = dict(profiles if profiles is not None else KNOWN_BUILD_PROFILES)
            if fingerprint not in selected_profiles:
                local_profile = load_local_build_profile(fingerprint, profile_root)
                if local_profile is not None:
                    selected_profiles[fingerprint] = local_profile
            if fingerprint not in selected_profiles and auto_audit and header[:2] == b"MZ":
                try:
                    selected_profiles[fingerprint] = (auditor or audit_build_profile)(
                        backend=backend,
                        handle=handle,
                        module=module,
                        executable_path=executable_path,
                        fingerprint=fingerprint,
                        seed_profiles=selected_profiles,
                        profile_root=profile_root,
                    )
                    result["auto_audit"] = {"status": "admitted-local"}
                except Exception as error:
                    result["auto_audit"] = {
                        "status": "failed",
                        "error": {"type": type(error).__name__, "message": str(error)},
                    }
            result["build"] = build_support(
                fingerprint,
                selected_profiles,
            )
            if header[:2] != b"MZ":
                result["status"] = "memory_validation_failed"
            elif not result["build"]["supported"]:
                result["status"] = "unsupported_build"
            elif not result["build"]["offsets_complete"]:
                result["status"] = "profile_incomplete"
            else:
                profile = selected_profiles[fingerprint]
                live_sample = resolve_player_sample(
                    backend,
                    handle,
                    module,
                    profile,
                )
                live_sample["waypoint_unlock_state"] = waypoint_unlock_diagnostic(
                    backend,
                    handle,
                    module,
                    profile,
                    int(live_sample["chain"]["player_controller"], 16),
                )
                live_sample["alpha_first_clear_state"] = alpha_first_clear_diagnostic(
                    backend,
                    handle,
                    module,
                    profile,
                    int(live_sample["chain"]["player_controller"], 16),
                )
                result["live_sample"] = live_sample
                result["status"] = "live_sample_ready"
        finally:
            backend.close_process(handle)
    except Exception as error:
        detail = {"type": type(error).__name__, "message": str(error)}
        if isinstance(error, Win32ProbeError):
            detail.update(operation=error.operation, win32_error=error.error_code)
        result.update(status="probe_failed", error=detail)
    return result


def main() -> None:
    print(json.dumps(probe_palworld(auto_audit=True), indent=2))


if __name__ == "__main__":
    main()
