from pathlib import Path
import json
import struct

from palworld_companion.runtime_audit import (
    discover_gworld_candidates,
    discover_inline_fname_tables,
    local_profile_path,
    read_local_profile,
    write_local_profile,
)
from palworld_companion.telemetry import (
    BuildProfile,
    KNOWN_BUILD_PROFILES,
    ModuleInfo,
    PalworldLiveReader,
    PROCESS_CREATE_THREAD,
    PROCESS_QUERY_LIMITED_INFORMATION,
    PROCESS_VM_OPERATION,
    PROCESS_VM_READ,
    PROCESS_VM_WRITE,
    READ_ONLY_PROCESS_ACCESS,
    ProcessInfo,
    _waypoint_comparison_indices,
    audit_build_profile,
    build_support,
    probe_palworld,
    resolve_waypoint_unlock_state,
    resolve_alpha_first_clear_state,
)


CURRENT_STEAM_SHA256 = "2FF94A03BC777661BE100249B4940242F70661D890C6B8F8ACA4D6DCE79EE5A5"


def write_test_pe(path: Path, section_name: str, section_rva: int, section_data: bytes) -> None:
    raw_offset = 0x400
    content = bytearray(raw_offset + len(section_data))
    content[:2] = b"MZ"
    struct.pack_into("<I", content, 0x3C, 0x80)
    content[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", content, 0x80 + 6, 1)
    struct.pack_into("<H", content, 0x80 + 20, 0xF0)
    optional_offset = 0x80 + 24
    struct.pack_into("<H", content, optional_offset, 0x20B)
    struct.pack_into("<I", content, optional_offset + 56, 0x10000)
    section_offset = optional_offset + 0xF0
    content[section_offset:section_offset + 8] = section_name.encode("ascii").ljust(8, b"\0")
    struct.pack_into(
        "<IIII",
        content,
        section_offset + 8,
        len(section_data),
        section_rva,
        len(section_data),
        raw_offset,
    )
    content[raw_offset:] = section_data
    path.write_bytes(content)


class FakeBackend:
    def __init__(self, executable: Path, header: bytes = b"MZ" + bytes(62)) -> None:
        self.executable = executable
        self.header = header
        self.closed = False

    def find_process(self, executable_name: str):
        return ProcessInfo(pid=42, name=executable_name)

    def open_process(self, pid: int) -> int:
        assert pid == 42
        return 7

    def executable_path(self, handle: int) -> Path:
        assert handle == 7
        return self.executable

    def main_module(self, pid: int) -> ModuleInfo:
        return ModuleInfo(
            name=self.executable.name,
            base_address=0x140000000,
            size=1234,
            path=str(self.executable),
        )

    def read(self, handle: int, address: int, size: int) -> bytes:
        assert handle == 7
        assert address == 0x140000000
        assert size <= len(self.header)
        return self.header[:size]

    def close_process(self, handle: int) -> None:
        assert handle == 7
        self.closed = True


class MissingProcessBackend(FakeBackend):
    def find_process(self, executable_name: str):
        return None


def test_process_access_mask_is_strictly_read_only():
    assert READ_ONLY_PROCESS_ACCESS == PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION
    assert not READ_ONLY_PROCESS_ACCESS & PROCESS_VM_WRITE
    assert not READ_ONLY_PROCESS_ACCESS & PROCESS_VM_OPERATION
    assert not READ_ONLY_PROCESS_ACCESS & PROCESS_CREATE_THREAD


def test_unknown_build_fails_closed_after_proving_memory_access(tmp_path):
    executable = tmp_path / "Palworld-Win64-Shipping.exe"
    executable.write_bytes(b"test executable")
    backend = FakeBackend(executable)

    result = probe_palworld(backend=backend, profiles={})

    assert result["status"] == "unsupported_build"
    assert result["memory_check"]["valid_pe_header"] is True
    assert result["build"]["supported"] is False
    assert backend.closed is True


def test_matching_fingerprint_is_explicitly_profile_gated(tmp_path):
    executable = tmp_path / "Palworld-Win64-Shipping.exe"
    executable.write_bytes(b"known build")
    first = probe_palworld(backend=FakeBackend(executable), profiles={})
    fingerprint = first["executable"]["sha256"]
    profiles = {
        fingerprint: BuildProfile(
            profile_id="test-profile",
            sha256=fingerprint,
            description="test-only verified profile",
        )
    }

    result = probe_palworld(backend=FakeBackend(executable), profiles=profiles)

    assert result["status"] == "profile_incomplete"
    assert result["build"]["profile_id"] == "test-profile"


def test_live_reader_runs_the_local_auditor_for_an_unknown_hash(tmp_path):
    executable = tmp_path / "Palworld-Win64-Shipping.exe"
    executable.write_bytes(b"unknown executable")
    backend = FakeBackend(executable)
    calls = []

    def auditor(**kwargs):
        calls.append(kwargs)
        return BuildProfile(
            profile_id="local-auto-audit",
            sha256=kwargs["fingerprint"],
            description="locally validated",
            offsets={"gworld_rva": 0x1234},
        )

    reader = PalworldLiveReader(
        backend=backend,
        profiles={},
        profile_root=tmp_path,
        auditor=auditor,
    )
    try:
        assert reader.profile.profile_id == "local-auto-audit"
        assert len(calls) == 1
        assert calls[0]["executable_path"] == executable
        assert calls[0]["profile_root"] == tmp_path
    finally:
        reader.close()

    assert backend.closed is True


def test_live_reader_reports_a_failed_auto_audit_and_closes_the_handle(tmp_path):
    executable = tmp_path / "Palworld-Win64-Shipping.exe"
    executable.write_bytes(b"unknown executable")
    backend = FakeBackend(executable)

    def auditor(**kwargs):
        raise RuntimeError("no unique GWorld candidate")

    try:
        PalworldLiveReader(backend=backend, profiles={}, profile_root=tmp_path, auditor=auditor)
    except RuntimeError as error:
        assert "Unsupported Palworld executable fingerprint" in str(error)
        assert "no unique GWorld candidate" in str(error)
    else:
        raise AssertionError("A failed local audit must remain fail-closed")

    assert backend.closed is True


def test_game_not_running_is_a_clean_observable_state(tmp_path):
    backend = MissingProcessBackend(tmp_path / "unused.exe")

    result = probe_palworld(backend=backend, profiles={})

    assert result["status"] == "game_not_running"
    assert "error" not in result


def test_build_support_is_case_insensitive():
    profile = BuildProfile(profile_id="one", sha256="ABCD", description="known")

    assert build_support("abcd", {"ABCD": profile})["supported"] is True


def test_current_steam_build_has_the_audited_runtime_profile():
    profile = KNOWN_BUILD_PROFILES[CURRENT_STEAM_SHA256]

    assert profile.profile_id == "steam-1.0-build-24181527"
    assert profile.offsets["gworld_rva"] == 0x965BBE0
    assert profile.offsets["fname_pool_rva"] == 0x944DB80
    assert profile.offsets["fname_pool_chunks"] == 0x10
    assert profile.offsets["fname_pool_chunks_indirect"] == 0


def test_gworld_discovery_resolves_a_rip_relative_candidate(tmp_path):
    executable = tmp_path / "Palworld-Win64-Shipping.exe"
    section = bytearray(0x200)
    instruction_rva = 0x1020
    target_rva = 0x2500
    section[0x20:0x2F] = bytes.fromhex("48 8B 1D 00 00 00 00 48 85 DB 74 3B 41 B0 01")
    struct.pack_into("<i", section, 0x23, target_rva - (instruction_rva + 7))
    write_test_pe(executable, ".text", 0x1000, section)

    candidates = discover_gworld_candidates(executable)

    assert list(candidates) == [target_rva]
    assert candidates[target_rva][0]["instruction_rva"] == instruction_rva


def test_local_audit_profile_round_trips_only_for_its_exact_hash(tmp_path):
    payload = {
        "schema_version": 1,
        "status": "admitted-local",
        "profile": {
            "profile_id": "local-test",
            "sha256": "ABCD",
            "description": "test",
            "offsets": {"gworld_rva": 0x1234},
        },
    }

    path = write_local_profile("ABCD", payload, tmp_path)

    assert path.is_file()
    assert read_local_profile("abcd", tmp_path) == payload
    assert read_local_profile("different", tmp_path) is None


def test_failed_local_audit_writes_an_inspectable_fail_closed_report(tmp_path):
    executable = tmp_path / "Palworld-Win64-Shipping.exe"
    executable.write_bytes(b"not used because no seed exists")

    try:
        audit_build_profile(
            backend=None,
            handle=7,
            module=ModuleInfo(executable.name, 0x140000000, 0x1000, str(executable)),
            executable_path=executable,
            fingerprint="ABCD",
            seed_profiles={},
            profile_root=tmp_path,
        )
    except RuntimeError as error:
        assert "No verified seed profile" in str(error)
    else:
        raise AssertionError("An audit without a verified seed must fail closed")

    report = json.loads(local_profile_path("ABCD", tmp_path).read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["audit"]["network_used"] is False
    assert report["audit"]["write_access_requested"] is False
    assert "No verified seed profile" in report["error"]["message"]
    assert read_local_profile("ABCD", tmp_path) is None


class SparseMemoryBackend:
    def __init__(self) -> None:
        self.segments: list[tuple[int, bytes]] = []

    def write(self, address: int, value: bytes) -> None:
        self.segments.append((address, value))

    def read(self, handle: int, address: int, size: int) -> bytes:
        assert handle == 7
        for start, value in reversed(self.segments):
            offset = address - start
            if 0 <= offset and offset + size <= len(value):
                return value[offset:offset + size]
        raise RuntimeError(f"test memory has no segment for 0x{address:X}+{size}")


def test_inline_fname_table_discovery_uses_replicated_guid_landmarks(tmp_path):
    executable = tmp_path / "Palworld-Win64-Shipping.exe"
    write_test_pe(executable, ".data", 0x2000, bytes(0x200))
    backend = SparseMemoryBackend()
    module_base = 0x140000000
    chunk_address = 0x450000
    comparison_indices = [0x20, 0x40]
    keys = (
        "596996B948716D3FD2283C8B5C6E829C",
        "4C204C3842EAB210A7A9DA9D2CF9CBBE",
    )
    section = bytearray(0x200)
    struct.pack_into("<Q", section, 0x40, chunk_address)
    backend.write(module_base + 0x2000, bytes(section))
    backend.write(chunk_address, struct.pack("<H", 4 << 6) + b"None")
    for comparison_index, key in zip(comparison_indices, keys):
        backend.write(
            chunk_address + (comparison_index << 1),
            struct.pack("<H", len(key) << 6) + key.encode("ascii"),
        )

    candidates = discover_inline_fname_tables(
        backend,
        7,
        module_base,
        executable,
        comparison_indices,
    )

    assert candidates == [{
        "table_rva": 0x2040,
        "sampled_names": list(keys),
        "sample_count": 2,
        "anchor": "FName[0]=None",
    }]


def test_inline_fname_table_discovery_does_not_require_progression_records(tmp_path):
    executable = tmp_path / "Palworld-Win64-Shipping.exe"
    write_test_pe(executable, ".data", 0x2000, bytes(0x200))
    backend = SparseMemoryBackend()
    module_base = 0x140000000
    chunk_address = 0x450000
    section = bytearray(0x200)
    struct.pack_into("<Q", section, 0x40, chunk_address)
    backend.write(module_base + 0x2000, bytes(section))
    backend.write(chunk_address, struct.pack("<H", 4 << 6) + b"None")

    candidates = discover_inline_fname_tables(
        backend,
        7,
        module_base,
        executable,
        [],
    )

    assert candidates == [{
        "table_rva": 0x2040,
        "sampled_names": [],
        "sample_count": 0,
        "anchor": "FName[0]=None",
    }]


def test_auto_audit_accepts_an_empty_progression_record_array():
    backend = SparseMemoryBackend()
    profile = BuildProfile(
        profile_id="zero-state-test",
        sha256="TEST",
        description="test",
        offsets={
            "controller_player_state": 0x10,
            "player_state_record_data": 0x20,
            "record_fast_travel_unlock_array": 0x30,
            "rep_bool_items": 0x18,
            "rep_bool_item_stride": 0x20,
            "rep_bool_item_key": 0x04,
        },
    )
    controller, player_state, record_data = 0x400000, 0x410000, 0x420000
    backend.write(controller + 0x10, struct.pack("<Q", player_state))
    backend.write(player_state + 0x20, struct.pack("<Q", record_data))
    backend.write(record_data + 0x30 + 0x18, struct.pack("<QII", 0, 0, 0))

    indices, count = _waypoint_comparison_indices(backend, 7, profile, controller)

    assert indices == []
    assert count == 0


def test_waypoint_unlock_state_decodes_guid_keys_and_boolean_values():
    backend = SparseMemoryBackend()
    module = ModuleInfo("Palworld.exe", 0x140000000, 0x10000, "Palworld.exe")
    profile = BuildProfile(
        profile_id="unlock-test",
        sha256="TEST",
        description="test",
        offsets={
            "controller_player_state": 0x10,
            "player_state_record_data": 0x20,
            "record_fast_travel_unlock_array": 0x30,
            "rep_bool_items": 0x18,
            "rep_bool_item_stride": 0x20,
            "rep_bool_item_key": 0x04,
            "rep_bool_item_value": 0x0C,
            "fname_pool_rva": 0x900,
            "fname_pool_chunks": 0x100,
        },
    )
    controller = 0x400000
    player_state = 0x410000
    record_data = 0x420000
    items_address = 0x430000
    chunks_address = 0x440000
    chunk_address = 0x450000
    first_key = "596996B948716D3FD2283C8B5C6E829C"
    second_key = "4C204C3842EAB210A7A9DA9D2CF9CBBE"
    first_index = 0x20
    second_index = 0x40

    backend.write(controller + 0x10, struct.pack("<Q", player_state))
    backend.write(player_state + 0x20, struct.pack("<Q", record_data))
    backend.write(record_data + 0x30 + 0x18, struct.pack("<QII", items_address, 2, 2))
    items = bytearray(0x40)
    struct.pack_into("<I", items, 0x04, first_index)
    items[0x0C] = 1
    struct.pack_into("<I", items, 0x20 + 0x04, second_index)
    items[0x20 + 0x0C] = 0
    backend.write(items_address, bytes(items))
    backend.write(module.base_address + 0x900 + 0x100, struct.pack("<Q", chunks_address))
    backend.write(chunks_address, struct.pack("<Q", chunk_address))
    backend.write(
        chunk_address + (first_index << 1),
        struct.pack("<H", len(first_key) << 6) + first_key.encode("ascii"),
    )
    backend.write(
        chunk_address + (second_index << 1),
        struct.pack("<H", len(second_key) << 6) + second_key.encode("ascii"),
    )

    state = resolve_waypoint_unlock_state(backend, 7, module, profile, controller)

    assert state["status"] == "ready"
    assert state["record_count"] == 2
    assert state["decoded_count"] == 2
    assert state["unlocked_keys"] == [first_key]


def test_waypoint_unlock_state_supports_an_inline_fname_block_table():
    backend = SparseMemoryBackend()
    module = ModuleInfo("Palworld.exe", 0x140000000, 0x10000, "Palworld.exe")
    profile = BuildProfile(
        profile_id="inline-name-pool-test",
        sha256="TEST",
        description="test",
        offsets={
            "controller_player_state": 0x10,
            "player_state_record_data": 0x20,
            "record_fast_travel_unlock_array": 0x30,
            "rep_bool_items": 0x18,
            "rep_bool_item_stride": 0x20,
            "rep_bool_item_key": 0x04,
            "rep_bool_item_value": 0x0C,
            "fname_pool_rva": 0x900,
            "fname_pool_chunks": 0x10,
            "fname_pool_chunks_indirect": 0,
        },
    )
    controller, player_state, record_data = 0x400000, 0x410000, 0x420000
    items_address, chunk_address = 0x430000, 0x450000
    key = "596996B948716D3FD2283C8B5C6E829C"
    comparison_index = 0x20

    backend.write(controller + 0x10, struct.pack("<Q", player_state))
    backend.write(player_state + 0x20, struct.pack("<Q", record_data))
    backend.write(record_data + 0x30 + 0x18, struct.pack("<QII", items_address, 1, 1))
    items = bytearray(0x20)
    struct.pack_into("<I", items, 0x04, comparison_index)
    items[0x0C] = 1
    backend.write(items_address, bytes(items))
    backend.write(module.base_address + 0x900 + 0x10, struct.pack("<Q", chunk_address))
    backend.write(
        chunk_address + (comparison_index << 1),
        struct.pack("<H", len(key) << 6) + key.encode("ascii"),
    )

    state = resolve_waypoint_unlock_state(backend, 7, module, profile, controller)

    assert state["status"] == "ready"
    assert state["unlocked_keys"] == [key]


def test_waypoint_unlock_state_rejects_non_guid_names():
    backend = SparseMemoryBackend()
    module = ModuleInfo("Palworld.exe", 0x140000000, 0x10000, "Palworld.exe")
    profile = BuildProfile(
        profile_id="unlock-test",
        sha256="TEST",
        description="test",
        offsets={
            "controller_player_state": 0x10,
            "player_state_record_data": 0x20,
            "record_fast_travel_unlock_array": 0x30,
            "rep_bool_items": 0x18,
            "rep_bool_item_stride": 0x20,
            "rep_bool_item_key": 0x04,
            "rep_bool_item_value": 0x0C,
            "fname_pool_rva": 0x900,
            "fname_pool_chunks": 0x100,
        },
    )
    controller, player_state, record_data = 0x400000, 0x410000, 0x420000
    items_address, chunks_address, chunk_address = 0x430000, 0x440000, 0x450000
    comparison_index = 0x20
    backend.write(controller + 0x10, struct.pack("<Q", player_state))
    backend.write(player_state + 0x20, struct.pack("<Q", record_data))
    backend.write(record_data + 0x30 + 0x18, struct.pack("<QII", items_address, 1, 1))
    items = bytearray(0x20)
    struct.pack_into("<I", items, 0x04, comparison_index)
    items[0x0C] = 1
    backend.write(items_address, bytes(items))
    backend.write(module.base_address + 0x900 + 0x100, struct.pack("<Q", chunks_address))
    backend.write(chunks_address, struct.pack("<Q", chunk_address))
    backend.write(
        chunk_address + (comparison_index << 1),
        struct.pack("<H", 8 << 6) + b"FTPoint1",
    )

    try:
        resolve_waypoint_unlock_state(backend, 7, module, profile, controller)
    except RuntimeError as error:
        assert "32-digit GUID" in str(error)
    else:
        raise AssertionError("Non-GUID runtime keys must fail closed")


def test_alpha_first_clear_state_decodes_numbered_fname_keys():
    backend = SparseMemoryBackend()
    module = ModuleInfo("Palworld.exe", 0x140000000, 0x10000, "Palworld.exe")
    profile = BuildProfile(
        profile_id="alpha-clear-test",
        sha256="TEST",
        description="test",
        offsets={
            "controller_player_state": 0x10,
            "player_state_record_data": 0x20,
            "record_normal_boss_defeat_array": 0x40,
            "rep_bool_items": 0x18,
            "rep_bool_item_stride": 0x20,
            "rep_bool_item_key": 0x04,
            "rep_bool_item_value": 0x0C,
            "fname_pool_rva": 0x900,
            "fname_pool_chunks": 0x100,
        },
    )
    controller, player_state, record_data = 0x400000, 0x410000, 0x420000
    items_address, chunks_address, chunk_address = 0x430000, 0x440000, 0x450000
    comparison_index = 0x20
    base_name = "81_1_grass_FBOSS"
    backend.write(controller + 0x10, struct.pack("<Q", player_state))
    backend.write(player_state + 0x20, struct.pack("<Q", record_data))
    backend.write(record_data + 0x40 + 0x18, struct.pack("<QII", items_address, 2, 2))
    items = bytearray(0x40)
    struct.pack_into("<II", items, 0x04, comparison_index, 15)
    items[0x0C] = 1
    struct.pack_into("<II", items, 0x20 + 0x04, comparison_index, 2)
    items[0x20 + 0x0C] = 0
    backend.write(items_address, bytes(items))
    backend.write(module.base_address + 0x900 + 0x100, struct.pack("<Q", chunks_address))
    backend.write(chunks_address, struct.pack("<Q", chunk_address))
    backend.write(
        chunk_address + (comparison_index << 1),
        struct.pack("<H", len(base_name) << 6) + base_name.encode("ascii"),
    )

    state = resolve_alpha_first_clear_state(backend, 7, module, profile, controller)

    assert state["status"] == "ready"
    assert state["record_count"] == 2
    assert state["cleared_keys"] == ["81_1_GRASS_FBOSS_14"]
