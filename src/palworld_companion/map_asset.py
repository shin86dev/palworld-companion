from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from PIL import Image

from .pak_reader import read_pak_files


class MapAssetError(RuntimeError):
    pass


@dataclass(frozen=True)
class TextureProfile:
    profile_id: str
    uexp_sha256: str
    uexp_size: int
    width: int
    height: int
    payload_offset: int
    payload_size: int
    trailer_size: int
    pixel_format: bytes = b"PF_DXT1\x00"


PALWORLD_1_0_WORLD_MAP = TextureProfile(
    profile_id="steam-1.0-2026-07-10-world-map",
    uexp_sha256="3B603EFB5891D8C02A09C334085FCCA832EB568EC92AEAE62DA9F2F53A0555AD",
    uexp_size=33_554_588,
    width=8192,
    height=8192,
    payload_offset=128,
    payload_size=33_554_432,
    trailer_size=28,
)

PALWORLD_1_0_TREE_MAP = TextureProfile(
    profile_id="steam-1.0-2026-07-10-tree-map",
    uexp_sha256="98020CA92609C44F431B26307512C0B6A3497EB9721D029109031D7E7D7DF127",
    uexp_size=33_554_588,
    width=8192,
    height=8192,
    payload_offset=128,
    payload_size=33_554_432,
    trailer_size=28,
)

WORLD_MAP_UASSET_PATH = "Pal/Content/Pal/Texture/UI/Map/T_WorldMap.uasset"
WORLD_MAP_UEXP_PATH = "Pal/Content/Pal/Texture/UI/Map/T_WorldMap.uexp"
WORLD_MAP_UASSET_SIZE = 674
WORLD_MAP_UASSET_SHA256 = "AFD5869FC3F41F850E29A328935FA181D351020C9E9249192689F124D1346632"
TREE_MAP_UASSET_PATH = "Pal/Content/Pal/Texture/UI/Map/T_TreeMap.uasset"
TREE_MAP_UEXP_PATH = "Pal/Content/Pal/Texture/UI/Map/T_TreeMap.uexp"
TREE_MAP_UASSET_SIZE = 671
TREE_MAP_UASSET_SHA256 = "398E93EA1E248CD79190C2C8EBDC20D8181D662DDBD70FD8859EFAECD5299BD1"
PALWORLD_APP_ID = "1623730"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def bytes_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest().upper()


def app_data_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local")) / "PalworldCompanion"


def world_map_cache_path() -> Path:
    if override := os.environ.get("PALPLUS_MAP_CACHE"):
        return Path(override).expanduser()
    fingerprint = PALWORLD_1_0_WORLD_MAP.uexp_sha256[:12]
    return app_data_root() / "maps" / f"T_WorldMap-{fingerprint}.webp"


def tree_map_cache_path() -> Path:
    if override := os.environ.get("PALPLUS_TREE_MAP_CACHE"):
        return Path(override).expanduser()
    fingerprint = PALWORLD_1_0_TREE_MAP.uexp_sha256[:12]
    return app_data_root() / "maps" / f"T_TreeMap-{fingerprint}.webp"


def map_provision_status_path() -> Path:
    if os.environ.get("PALPLUS_MAP_CACHE"):
        return world_map_cache_path().with_suffix(".provision.json")
    return app_data_root() / "map-provision.json"


def tree_map_provision_status_path() -> Path:
    if os.environ.get("PALPLUS_TREE_MAP_CACHE"):
        return tree_map_cache_path().with_suffix(".provision.json")
    return app_data_root() / "tree-map-provision.json"


def world_map_cache_is_ready() -> bool:
    cache_path = world_map_cache_path()
    status_path = map_provision_status_path()
    if not cache_path.is_file() or not status_path.is_file():
        return False
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return (
            status.get("status") == "ready"
            and status.get("profile_id") == PALWORLD_1_0_WORLD_MAP.profile_id
            and Path(status.get("cache_path", "")) == cache_path
            and status.get("output_sha256") == file_sha256(cache_path)
        )
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def tree_map_cache_is_ready() -> bool:
    cache_path = tree_map_cache_path()
    status_path = tree_map_provision_status_path()
    if not cache_path.is_file() or not status_path.is_file():
        return False
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return (
            status.get("status") == "ready"
            and status.get("profile_id") == PALWORLD_1_0_TREE_MAP.profile_id
            and Path(status.get("cache_path", "")) == cache_path
            and status.get("output_sha256") == file_sha256(cache_path)
        )
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    for environment_name in ("ProgramFiles(x86)", "ProgramFiles"):
        if base := os.environ.get(environment_name):
            roots.append(Path(base) / "Steam")
    if sys.platform == "win32":
        try:
            import winreg

            registry_locations = (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam", "InstallPath"),
            )
            for hive, key_name, value_name in registry_locations:
                try:
                    with winreg.OpenKey(hive, key_name) as key:
                        roots.append(Path(winreg.QueryValueEx(key, value_name)[0]))
                except OSError:
                    continue
        except ImportError:
            pass
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def _steam_libraries(steam_root: Path) -> list[Path]:
    libraries = [steam_root]
    registry = steam_root / "steamapps" / "libraryfolders.vdf"
    if not registry.is_file():
        return libraries
    try:
        content = registry.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libraries
    for value in re.findall(r'"path"\s+"([^"]+)"', content, flags=re.IGNORECASE):
        candidate = Path(value.replace("\\\\", "\\"))
        if candidate not in libraries:
            libraries.append(candidate)
    return libraries


def _pak_from_install_root(root: Path) -> Path:
    return root / "Pal" / "Content" / "Paks" / "Pal-Windows.pak"


def find_palworld_pak() -> Path | None:
    """Find the local Steam archive without requiring setup or server access."""
    if override := os.environ.get("PALPLUS_PAK_PATH"):
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() else None
    if install_override := os.environ.get("PALWORLD_INSTALL_DIR"):
        root = Path(install_override).expanduser()
        candidate = root if root.name.lower().endswith(".pak") else _pak_from_install_root(root)
        return candidate if candidate.is_file() else None

    for steam_root in _steam_roots():
        for library in _steam_libraries(steam_root):
            steamapps = library / "steamapps"
            install_name = "Palworld"
            manifest = steamapps / f"appmanifest_{PALWORLD_APP_ID}.acf"
            if manifest.is_file():
                try:
                    content = manifest.read_text(encoding="utf-8", errors="replace")
                    match = re.search(r'"installdir"\s+"([^"]+)"', content, flags=re.IGNORECASE)
                    if match:
                        install_name = match.group(1)
                except OSError:
                    pass
            candidate = _pak_from_install_root(steamapps / "common" / install_name)
            if candidate.is_file():
                return candidate
    return None


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    try:
        part.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def _status_payload(status: str, destination: Path, pak_path: Path | None, **extra) -> dict:
    def installed_version(package: str) -> str:
        try:
            return package_version(package)
        except PackageNotFoundError:
            return "not-installed"

    return {
        "status": status,
        "profile_id": PALWORLD_1_0_WORLD_MAP.profile_id,
        "pak_path": str(pak_path) if pak_path is not None else None,
        "cache_path": str(destination),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "network_used": False,
        "uploaded": False,
        "archive_reader": {
            "package": "pyuepak",
            "version": installed_version("pyuepak"),
            "mode": "read-only explicit file allow-list",
        },
        "decompressor": {
            "package": "pyooz",
            "version": installed_version("pyooz"),
            "adapter": "PalPlus in-memory open-source-only adapter",
        },
        **extra,
    }


def provision_world_map(
    pak_path: Path,
    destination: Path | None = None,
    *,
    profile: TextureProfile = PALWORLD_1_0_WORLD_MAP,
    expected_uasset_size: int = WORLD_MAP_UASSET_SIZE,
    expected_uasset_sha256: str = WORLD_MAP_UASSET_SHA256,
    output_size: int = 4096,
) -> Path:
    """Selectively extract, validate, and privately cache the installed world map."""
    destination = destination or world_map_cache_path()
    status_path = map_provision_status_path()
    temp_uexp = destination.with_suffix(".uexp.part")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        files = read_pak_files(pak_path, (WORLD_MAP_UASSET_PATH, WORLD_MAP_UEXP_PATH))
        uasset = files[WORLD_MAP_UASSET_PATH]
        uexp = files[WORLD_MAP_UEXP_PATH]
        if len(uasset) != expected_uasset_size:
            raise MapAssetError(
                f"World-map metadata size changed: {len(uasset)}, expected {expected_uasset_size}."
            )
        uasset_hash = bytes_sha256(uasset)
        if uasset_hash != expected_uasset_sha256:
            raise MapAssetError(
                f"World-map metadata fingerprint changed: {uasset_hash}, expected {expected_uasset_sha256}."
            )
        temp_uexp.write_bytes(uexp)
        decode_world_map_uexp(temp_uexp, destination, profile=profile, output_size=output_size)
        payload = _status_payload(
            "ready",
            destination,
            pak_path,
            profile_id=profile.profile_id,
            uasset_sha256=uasset_hash,
            uexp_sha256=bytes_sha256(uexp),
            output_sha256=file_sha256(destination),
        )
        _write_json_atomic(status_path, payload)
        return destination
    except Exception as error:
        _write_json_atomic(
            status_path,
            _status_payload(
                "failed", destination, pak_path, profile_id=profile.profile_id, error=str(error)
            ),
        )
        if isinstance(error, MapAssetError):
            raise
        raise MapAssetError(str(error)) from error
    finally:
        temp_uexp.unlink(missing_ok=True)


def provision_tree_map(
    pak_path: Path,
    destination: Path | None = None,
    *,
    profile: TextureProfile = PALWORLD_1_0_TREE_MAP,
    expected_uasset_size: int = TREE_MAP_UASSET_SIZE,
    expected_uasset_sha256: str = TREE_MAP_UASSET_SHA256,
    output_size: int = 4096,
) -> Path:
    """Selectively extract, validate, and privately cache the installed World Tree map."""
    destination = destination or tree_map_cache_path()
    status_path = tree_map_provision_status_path()
    temp_uexp = destination.with_suffix(".uexp.part")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        files = read_pak_files(pak_path, (TREE_MAP_UASSET_PATH, TREE_MAP_UEXP_PATH))
        uasset = files[TREE_MAP_UASSET_PATH]
        uexp = files[TREE_MAP_UEXP_PATH]
        if len(uasset) != expected_uasset_size:
            raise MapAssetError(
                f"World Tree map metadata size changed: {len(uasset)}, expected {expected_uasset_size}."
            )
        uasset_hash = bytes_sha256(uasset)
        if uasset_hash != expected_uasset_sha256:
            raise MapAssetError(
                f"World Tree map metadata fingerprint changed: {uasset_hash}, expected {expected_uasset_sha256}."
            )
        temp_uexp.write_bytes(uexp)
        decode_world_map_uexp(temp_uexp, destination, profile=profile, output_size=output_size)
        payload = _status_payload(
            "ready",
            destination,
            pak_path,
            profile_id=profile.profile_id,
            uasset_sha256=uasset_hash,
            uexp_sha256=bytes_sha256(uexp),
            output_sha256=file_sha256(destination),
            map_region="world-tree",
        )
        _write_json_atomic(status_path, payload)
        return destination
    except Exception as error:
        _write_json_atomic(
            status_path,
            _status_payload(
                "failed",
                destination,
                pak_path,
                profile_id=profile.profile_id,
                map_region="world-tree",
                error=str(error),
            ),
        )
        if isinstance(error, MapAssetError):
            raise
        raise MapAssetError(str(error)) from error
    finally:
        temp_uexp.unlink(missing_ok=True)


def map_provision_diagnostics() -> dict:
    pak_path = find_palworld_pak()
    cache_path = world_map_cache_path()
    status_path = map_provision_status_path()
    last_status = None
    if status_path.is_file():
        try:
            last_status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            last_status = {"status": "unreadable", "error": str(error)}
    return {
        "profile_id": PALWORLD_1_0_WORLD_MAP.profile_id,
        "pak_found": pak_path is not None,
        "pak_path": str(pak_path) if pak_path is not None else None,
        "cache_ready": world_map_cache_is_ready(),
        "cache_path": str(cache_path),
        "status_path": str(status_path),
        "last_status": last_status,
        "network_required": False,
        "tree_map": {
            "profile_id": PALWORLD_1_0_TREE_MAP.profile_id,
            "cache_ready": tree_map_cache_is_ready(),
            "cache_path": str(tree_map_cache_path()),
            "status_path": str(tree_map_provision_status_path()),
        },
    }


def bc1_dds_header(width: int, height: int, payload_size: int) -> bytes:
    """Build a legacy DDS header around a raw DXT1/BC1 payload."""
    ddsd_caps = 0x1
    ddsd_height = 0x2
    ddsd_width = 0x4
    ddsd_pixel_format = 0x1000
    ddsd_linear_size = 0x80000
    ddpf_fourcc = 0x4
    dds_caps_texture = 0x1000
    values = [
        124,
        ddsd_caps | ddsd_height | ddsd_width | ddsd_pixel_format | ddsd_linear_size,
        height,
        width,
        payload_size,
        0,
        1,
        *([0] * 11),
        32,
        ddpf_fourcc,
        int.from_bytes(b"DXT1", "little"),
        0,
        0,
        0,
        0,
        0,
        dds_caps_texture,
        0,
        0,
        0,
        0,
    ]
    header = b"DDS " + struct.pack("<31I", *values)
    if len(header) != 128:
        raise AssertionError(f"DDS header was {len(header)} bytes, expected 128.")
    return header


def decode_world_map_uexp(
    source: Path,
    destination: Path,
    profile: TextureProfile = PALWORLD_1_0_WORLD_MAP,
    output_size: int = 4096,
) -> Path:
    """Decode one exact, audited map payload into a private local WebP cache."""
    if source.stat().st_size != profile.uexp_size:
        raise MapAssetError(
            f"World-map payload size changed: {source.stat().st_size}, expected {profile.uexp_size}."
        )
    actual_hash = file_sha256(source)
    if actual_hash != profile.uexp_sha256:
        raise MapAssetError(
            f"World-map payload fingerprint changed: {actual_hash}, expected {profile.uexp_sha256}."
        )
    if profile.payload_offset + profile.payload_size + profile.trailer_size != profile.uexp_size:
        raise MapAssetError("The audited world-map profile has inconsistent byte ranges.")

    with source.open("rb") as stream:
        prefix = stream.read(profile.payload_offset)
    if profile.pixel_format not in prefix:
        raise MapAssetError("The audited DXT1 pixel-format marker is missing.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    dds_path = destination.with_suffix(".dds.part")
    output_path = destination.with_suffix(destination.suffix + ".part")
    try:
        with source.open("rb") as input_stream, dds_path.open("wb") as output_stream:
            output_stream.write(bc1_dds_header(profile.width, profile.height, profile.payload_size))
            input_stream.seek(profile.payload_offset)
            remaining = profile.payload_size
            while remaining:
                chunk = input_stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise MapAssetError("World-map payload ended before the audited byte range.")
                output_stream.write(chunk)
                remaining -= len(chunk)

        with Image.open(dds_path) as image:
            image.load()
            if image.size != (profile.width, profile.height):
                raise MapAssetError(f"Decoded map size was {image.size}, expected {(profile.width, profile.height)}.")
            if max(image.size) > output_size:
                image.thumbnail((output_size, output_size), Image.Resampling.LANCZOS)
            image.save(output_path, "WEBP", quality=72, method=4)
        os.replace(output_path, destination)
    finally:
        dds_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
    return destination


def main() -> int:
    diagnostics = map_provision_diagnostics()
    if "--provision" in sys.argv:
        pak_path = find_palworld_pak()
        if pak_path is None:
            diagnostics["provision_error"] = "Palworld's installed Steam archive was not found."
            print(json.dumps(diagnostics, indent=2))
            return 1
        try:
            provision_world_map(pak_path)
            provision_tree_map(pak_path)
        except MapAssetError as error:
            diagnostics = map_provision_diagnostics()
            diagnostics["provision_error"] = str(error)
            print(json.dumps(diagnostics, indent=2))
            return 1
        diagnostics = map_provision_diagnostics()
    print(json.dumps(diagnostics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
