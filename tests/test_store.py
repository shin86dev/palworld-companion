from pathlib import Path

import pytest

from palworld_companion.store import Store


def test_unlock_and_export_import(tmp_path: Path):
    store = Store(tmp_path / "one.sqlite")
    store.set_unlocked("node-a", True)
    store.save_profile({"level": 73, "tier": "endgame"})
    store.save_map_destination("node-b")
    store.set_onboarding_complete(True)
    payload = store.export_state()
    restored = Store(tmp_path / "two.sqlite")
    restored.import_state(payload, {"node-a", "node-b"})
    assert restored.unlocked_ids() == {"node-a"}
    assert restored.load_profile() == {"level": 73, "tier": "endgame"}
    assert restored.load_map_destination() == "node-b"
    assert restored.is_onboarding_complete() is True


def test_import_rejects_unknown_node(tmp_path: Path):
    store = Store(tmp_path / "state.sqlite")
    with pytest.raises(ValueError, match="unknown"):
        store.import_state({"format": 1, "unlocked_fast_travel": ["bad"]}, {"good"})


def test_first_run_and_profile_state_start_empty_and_persist(tmp_path: Path):
    path = tmp_path / "state.sqlite"
    store = Store(path)

    assert store.is_onboarding_complete() is False
    assert store.load_profile() is None

    store.save_profile({"level": 52, "tier": "late"})
    store.set_onboarding_complete(True)
    store.close()

    reopened = Store(path)
    assert reopened.is_onboarding_complete() is True
    assert reopened.load_profile() == {"level": 52, "tier": "late"}


def test_map_destination_starts_empty_and_persists(tmp_path: Path):
    path = tmp_path / "state.sqlite"
    store = Store(path)

    assert store.load_map_destination() is None
    store.save_map_destination("target-node")
    store.close()

    reopened = Store(path)
    assert reopened.load_map_destination() == "target-node"
    reopened.clear_map_destination()
    assert reopened.load_map_destination() is None


def test_legacy_manual_path_migrates_to_destination(tmp_path: Path):
    store = Store(tmp_path / "state.sqlite")
    store.save_map_path("old-start", "kept-target")

    assert store.load_map_destination() == "kept-target"
    assert store.load_map_path() is None


def test_overlay_position_starts_empty_and_persists(tmp_path: Path):
    path = tmp_path / "state.sqlite"
    store = Store(path)

    assert store.load_overlay_position() is None
    store.save_overlay_position(1200, 40)
    store.close()

    reopened = Store(path)
    assert reopened.load_overlay_position() == (1200, 40)
    reopened.clear_overlay_position()
    assert reopened.load_overlay_position() is None


def test_overlay_anchor_is_normalized_and_persists(tmp_path: Path):
    path = tmp_path / "state.sqlite"
    store = Store(path)

    assert store.load_overlay_anchor() == (1.0, 0.0)
    store.save_overlay_anchor(0.25, 0.75)
    store.close()

    reopened = Store(path)
    assert reopened.load_overlay_anchor() == (0.25, 0.75)
    with pytest.raises(ValueError, match="normalized"):
        reopened.save_overlay_anchor(1.1, 0.5)


def test_minimap_zoom_starts_at_default_and_persists(tmp_path: Path):
    path = tmp_path / "state.sqlite"
    store = Store(path)

    assert store.load_minimap_zoom() == 40
    store.save_minimap_zoom(67)
    store.close()

    reopened = Store(path)
    assert reopened.load_minimap_zoom() == 67
    with pytest.raises(ValueError, match="0 to 100"):
        reopened.save_minimap_zoom(101)


def test_alpha_pal_layer_is_default_on_and_persists(tmp_path: Path):
    path = tmp_path / "state.sqlite"
    store = Store(path)

    assert store.load_map_layer_visibility("alpha_pals") is True
    store.save_map_layer_visibility("alpha_pals", False)
    store.close()

    reopened = Store(path)
    assert reopened.load_map_layer_visibility("alpha_pals") is False


def test_custom_coordinate_destination_persists_without_becoming_an_unlock(tmp_path: Path):
    path = tmp_path / "state.sqlite"
    store = Store(path)
    target = {
        "id": "coordinate:palpagos:-134:-95",
        "name": "Coordinates (-134, -95)",
        "kind": "map_coordinate",
        "region": "palpagos",
        "coordinate_status": "verified",
        "coordinate_system": "palpagos-display-v1",
        "x": -134,
        "y": -95,
        "world_x": -167230,
        "world_y": 96494,
    }

    store.save_custom_map_destination(target)
    store.close()

    reopened = Store(path)
    assert reopened.load_custom_map_destination() == target
    assert reopened.load_map_destination() is None
    assert reopened.unlocked_ids() == set()
