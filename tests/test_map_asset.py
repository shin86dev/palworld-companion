import hashlib
import json

import pytest
from PIL import Image

from palworld_companion.map_asset import (
    MapAssetError,
    TextureProfile,
    bc1_dds_header,
    decode_world_map_uexp,
    find_palworld_pak,
    map_provision_diagnostics,
    map_provision_status_path,
    provision_world_map,
    world_map_cache_is_ready,
    world_map_cache_path,
)
import palworld_companion.map_asset as map_asset_module


def tiny_profile(payload: bytes, header: bytes, trailer: bytes) -> TextureProfile:
    content = header + payload + trailer
    return TextureProfile(
        profile_id="test-dxt1",
        uexp_sha256=hashlib.sha256(content).hexdigest().upper(),
        uexp_size=len(content),
        width=4,
        height=4,
        payload_offset=len(header),
        payload_size=len(payload),
        trailer_size=len(trailer),
    )


def test_bc1_dds_header_has_expected_shape():
    header = bc1_dds_header(4, 4, 8)

    assert len(header) == 128
    assert header[:4] == b"DDS "
    assert header[84:88] == b"DXT1"


def test_decode_world_map_uexp_converts_a_validated_bc1_payload(tmp_path):
    header = b"PF_DXT1\x00" + bytes(24)
    red_bc1_block = bytes.fromhex("00f8000000000000")
    trailer = bytes(4)
    source = tmp_path / "map.uexp"
    source.write_bytes(header + red_bc1_block + trailer)
    destination = tmp_path / "map.webp"

    decode_world_map_uexp(
        source,
        destination,
        profile=tiny_profile(red_bc1_block, header, trailer),
        output_size=4,
    )

    with Image.open(destination) as image:
        assert image.size == (4, 4)
        red, green, blue = image.convert("RGB").getpixel((2, 2))
    assert red > 200
    assert green < 40
    assert blue < 40


def test_decode_world_map_uexp_abstains_on_changed_payload(tmp_path):
    header = b"PF_DXT1\x00" + bytes(24)
    payload = bytes.fromhex("00f8000000000000")
    trailer = bytes(4)
    source = tmp_path / "map.uexp"
    source.write_bytes(header + payload + trailer)
    profile = tiny_profile(payload, header, trailer)
    source.write_bytes(header + bytes.fromhex("e007000000000000") + trailer)
    destination = tmp_path / "map.webp"

    with pytest.raises(MapAssetError, match="fingerprint changed"):
        decode_world_map_uexp(source, destination, profile=profile, output_size=4)

    assert not destination.exists()


def test_map_cache_and_status_can_be_isolated_for_acceptance_runs(monkeypatch, tmp_path):
    cache = tmp_path / "private-map.webp"
    monkeypatch.setenv("PALPLUS_MAP_CACHE", str(cache))

    assert world_map_cache_path() == cache
    assert map_provision_status_path() == tmp_path / "private-map.provision.json"


def test_map_cache_requires_matching_success_metadata(monkeypatch, tmp_path):
    cache = tmp_path / "private-map.webp"
    cache.write_bytes(b"map")
    monkeypatch.setenv("PALPLUS_MAP_CACHE", str(cache))
    status_path = map_provision_status_path()
    status_path.write_text(
        json.dumps(
            {
                "status": "ready",
                "profile_id": map_asset_module.PALWORLD_1_0_WORLD_MAP.profile_id,
                "cache_path": str(cache),
                "output_sha256": "WRONG",
            }
        ),
        encoding="utf-8",
    )

    assert world_map_cache_is_ready() is False

    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["output_sha256"] = hashlib.sha256(b"map").hexdigest().upper()
    status_path.write_text(json.dumps(status), encoding="utf-8")

    assert world_map_cache_is_ready() is True


def test_find_palworld_pak_honors_explicit_read_only_path(monkeypatch, tmp_path):
    archive = tmp_path / "Pal-Windows.pak"
    archive.write_bytes(b"pak")
    monkeypatch.setenv("PALPLUS_PAK_PATH", str(archive))

    assert find_palworld_pak() == archive


def test_provision_world_map_validates_both_assets_and_writes_diagnostics(monkeypatch, tmp_path):
    header = b"PF_DXT1\x00" + bytes(24)
    payload = bytes.fromhex("00f8000000000000")
    trailer = bytes(4)
    uexp = header + payload + trailer
    uasset = b"audited metadata"
    profile = tiny_profile(payload, header, trailer)
    destination = tmp_path / "map.webp"
    archive = tmp_path / "Pal-Windows.pak"
    archive.write_bytes(b"pak")
    monkeypatch.setenv("PALPLUS_MAP_CACHE", str(destination))
    monkeypatch.setattr(map_asset_module, "PALWORLD_1_0_WORLD_MAP", profile)
    monkeypatch.setattr(
        map_asset_module,
        "read_pak_files",
        lambda _archive, paths: {
            map_asset_module.WORLD_MAP_UASSET_PATH: uasset,
            map_asset_module.WORLD_MAP_UEXP_PATH: uexp,
        },
    )

    result = provision_world_map(
        archive,
        destination,
        profile=profile,
        expected_uasset_size=len(uasset),
        expected_uasset_sha256=hashlib.sha256(uasset).hexdigest().upper(),
        output_size=4,
    )

    assert result == destination
    assert destination.is_file()
    status = json.loads(map_provision_status_path().read_text(encoding="utf-8"))
    assert status["status"] == "ready"
    assert status["network_used"] is False
    assert status["uploaded"] is False
    assert status["uexp_sha256"] == hashlib.sha256(uexp).hexdigest().upper()
    assert map_provision_diagnostics()["cache_ready"] is True


def test_provision_world_map_records_a_self_describing_failure(monkeypatch, tmp_path):
    destination = tmp_path / "map.webp"
    archive = tmp_path / "Pal-Windows.pak"
    archive.write_bytes(b"pak")
    monkeypatch.setenv("PALPLUS_MAP_CACHE", str(destination))
    monkeypatch.setattr(
        map_asset_module,
        "read_pak_files",
        lambda _archive, paths: {
            map_asset_module.WORLD_MAP_UASSET_PATH: b"changed",
            map_asset_module.WORLD_MAP_UEXP_PATH: b"unused",
        },
    )

    with pytest.raises(MapAssetError, match="metadata size changed"):
        provision_world_map(archive, destination)

    status = json.loads(map_provision_status_path().read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert "metadata size changed" in status["error"]
    assert not destination.exists()


def test_provision_tree_map_uses_its_own_exact_asset_profile(monkeypatch, tmp_path):
    header = b"PF_DXT1\x00" + bytes(24)
    payload = bytes.fromhex("00f8000000000000")
    trailer = bytes(4)
    uexp = header + payload + trailer
    uasset = b"audited tree metadata"
    profile = tiny_profile(payload, header, trailer)
    destination = tmp_path / "tree-map.webp"
    archive = tmp_path / "Pal-Windows.pak"
    archive.write_bytes(b"pak")
    monkeypatch.setenv("PALPLUS_TREE_MAP_CACHE", str(destination))
    monkeypatch.setattr(
        map_asset_module,
        "read_pak_files",
        lambda _archive, paths: {
            map_asset_module.TREE_MAP_UASSET_PATH: uasset,
            map_asset_module.TREE_MAP_UEXP_PATH: uexp,
        },
    )

    result = map_asset_module.provision_tree_map(
        archive,
        destination,
        profile=profile,
        expected_uasset_size=len(uasset),
        expected_uasset_sha256=hashlib.sha256(uasset).hexdigest().upper(),
        output_size=4,
    )

    assert result == destination
    assert destination.is_file()
    status = json.loads(map_asset_module.tree_map_provision_status_path().read_text(encoding="utf-8"))
    assert status["status"] == "ready"
    assert status["profile_id"] == profile.profile_id
    assert status["network_used"] is False
