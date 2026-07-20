"""Deterministic helpers for locally auditing an updated Palworld build.

The scanner only inspects the installed executable and bytes exposed through
PalPlus's existing read-only process handle. It does not patch, inject, write,
or download anything.
"""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_RIP_LOAD_NULL_TESTS = (
    ("05", "C0"),  # RAX
    ("0D", "C9"),  # RCX
    ("15", "D2"),  # RDX
    ("1D", "DB"),  # RBX
    ("2D", "ED"),  # RBP
    ("35", "F6"),  # RSI
    ("3D", "FF"),  # RDI
)
GWORLD_PATTERNS = tuple(
    pattern
    for load, test in _RIP_LOAD_NULL_TESTS
    for pattern in (
        f"48 8B {load} ? ? ? ? 48 85 {test} 74 ?",
        f"48 8B {load} ? ? ? ? 48 85 {test} 0F 84",
    )
)


@dataclass(frozen=True)
class PeSection:
    name: str
    rva: int
    virtual_size: int
    raw_offset: int
    raw_size: int


def parse_pe_sections(content: bytes) -> tuple[list[PeSection], int]:
    if len(content) < 0x40 or content[:2] != b"MZ":
        raise RuntimeError("The executable does not have a valid DOS header.")
    pe_offset = struct.unpack_from("<I", content, 0x3C)[0]
    if pe_offset + 24 > len(content) or content[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise RuntimeError("The executable does not have a valid PE header.")
    section_count = struct.unpack_from("<H", content, pe_offset + 6)[0]
    optional_size = struct.unpack_from("<H", content, pe_offset + 20)[0]
    optional_offset = pe_offset + 24
    if optional_offset + optional_size > len(content) or optional_size < 60:
        raise RuntimeError("The executable has an invalid PE optional header.")
    image_size = struct.unpack_from("<I", content, optional_offset + 56)[0]
    section_offset = optional_offset + optional_size
    sections: list[PeSection] = []
    for index in range(section_count):
        offset = section_offset + index * 40
        if offset + 40 > len(content):
            raise RuntimeError("The executable has a truncated PE section table.")
        name = content[offset:offset + 8].split(b"\0", 1)[0].decode("ascii", errors="replace")
        virtual_size, rva, raw_size, raw_offset = struct.unpack_from("<IIII", content, offset + 8)
        if raw_offset + raw_size > len(content):
            raise RuntimeError(f"PE section {name!r} extends beyond the executable.")
        sections.append(PeSection(name, rva, virtual_size, raw_offset, raw_size))
    return sections, image_size


def _pattern_regex(pattern: str) -> bytes:
    return b"".join(
        b"." if token == "?" else re.escape(bytes([int(token, 16)]))
        for token in pattern.split()
    )


def discover_gworld_candidates(executable_path: Path) -> dict[int, list[dict]]:
    """Resolve RIP-relative global candidates from version-resistant UE patterns."""
    content = executable_path.read_bytes()
    sections, image_size = parse_pe_sections(content)
    text = next((section for section in sections if section.name == ".text"), None)
    if text is None:
        raise RuntimeError("The executable has no .text section.")
    body = content[text.raw_offset:text.raw_offset + text.raw_size]
    candidates: dict[int, list[dict]] = {}
    for pattern_index, pattern in enumerate(GWORLD_PATTERNS):
        for match in re.finditer(_pattern_regex(pattern), body, flags=re.DOTALL):
            instruction_rva = text.rva + match.start()
            displacement = struct.unpack_from("<i", body, match.start() + 3)[0]
            target_rva = instruction_rva + 7 + displacement
            if not 0 < target_rva < image_size:
                continue
            candidates.setdefault(target_rva, []).append(
                {"pattern_index": pattern_index, "instruction_rva": instruction_rva}
            )
    return candidates


def pe_section(executable_path: Path, name: str) -> PeSection:
    sections, _ = parse_pe_sections(executable_path.read_bytes())
    section = next((candidate for candidate in sections if candidate.name == name), None)
    if section is None:
        raise RuntimeError(f"The executable has no {name} section.")
    return section


def _decode_fname_entry(backend, handle: int, chunk: int, comparison_index: int) -> str:
    byte_offset = (comparison_index & 0xFFFF) << 1
    header = struct.unpack("<H", backend.read(handle, chunk + byte_offset, 2))[0]
    length = (header >> 6) & 0x3FF
    is_wide = bool(header & 1)
    if not 1 <= length <= 256:
        raise RuntimeError(f"FName entry has an invalid length: {length}")
    raw = backend.read(handle, chunk + byte_offset + 2, length * (2 if is_wide else 1))
    return raw.decode("utf-16-le" if is_wide else "utf-8")


def discover_inline_fname_tables(
    backend,
    handle: int,
    module_base: int,
    executable_path: Path,
    comparison_indices: Iterable[int],
) -> list[dict]:
    """Find an inline FName block table, then validate available GUID landmarks."""
    indices = list(dict.fromkeys(int(value) for value in comparison_indices))[:12]
    section = pe_section(executable_path, ".data")
    data = backend.read(handle, module_base + section.rva, section.virtual_size)
    found: dict[int, dict] = {}
    for offset in range(0, len(data) - 7, 8):
        chunk = struct.unpack_from("<Q", data, offset)[0]
        if not 0x10000 <= chunk <= 0x7FFFFFFFFFFF or chunk & 1:
            continue
        try:
            anchor_name = _decode_fname_entry(backend, handle, chunk, 0)
        except Exception:
            continue
        if anchor_name != "None":
            continue
        table_rva = section.rva + offset
        if not 0 < table_rva < section.rva + section.virtual_size:
            continue
        decoded: list[str] = []
        try:
            for comparison_index in indices:
                chunk_index = comparison_index >> 16
                chunk_pointer = struct.unpack(
                    "<Q",
                    backend.read(handle, module_base + table_rva + chunk_index * 8, 8),
                )[0]
                name = _decode_fname_entry(backend, handle, chunk_pointer, comparison_index).upper()
                if re.fullmatch(r"[0-9A-F]{32}", name) is None:
                    raise RuntimeError("A sampled FName was not a GUID.")
                decoded.append(name)
        except Exception:
            continue
        found[table_rva] = {
            "table_rva": table_rva,
            "sampled_names": decoded,
            "sample_count": len(decoded),
            "anchor": "FName[0]=None",
        }
    return list(found.values())


def default_profile_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local"))
    return base / "PalworldCompanion" / "build-profiles"


def local_profile_path(fingerprint: str, root: Path | None = None) -> Path:
    return (root or default_profile_root()) / f"{fingerprint.upper()}.json"


def read_local_profile(fingerprint: str, root: Path | None = None) -> dict | None:
    path = local_profile_path(fingerprint, root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        payload.get("schema_version") != 1
        or payload.get("status") != "admitted-local"
        or payload.get("profile", {}).get("sha256", "").upper() != fingerprint.upper()
    ):
        return None
    return payload


def write_local_profile(fingerprint: str, payload: dict, root: Path | None = None) -> Path:
    path = local_profile_path(fingerprint, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".json.part")
    try:
        part.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    return path
