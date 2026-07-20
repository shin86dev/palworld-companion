import copy

import pytest

from palworld_companion.bundle import BundleError, load_bundle, validate_bundle


def bundle():
    return {
        "bundle_version": "test", "game_version": "1.0", "created": "2026-07-13",
        "sources": [{
            "id": "s", "name": "Source", "url": "https://example.test",
            "coverage": ["progression"], "reuse_status": "citation-only",
            "game_version": "1.0", "last_checked": "2026-07-13",
            "confidence": "verified", "contradictions": [],
        }],
        "facts": [{"id": "f", "title": "Fact", "category": "progression", "source_id": "s", "last_checked": "2026-07-13", "confidence": "verified"}],
        "locations": [{"id": "l", "name": "Node", "x": 10, "y": -20, "kind": "fast_travel", "waypoint_class": "standard", "reveals_map": False, "coordinate_status": "verified", "coordinate_system": "palpagos-map-v1", "source_id": "s", "last_checked": "2026-07-13", "confidence": "verified"}],
        "rules": [{"id": "r", "title": "Rule", "action": "Act", "rationale": "Because", "when": {"min_level": 1, "keywords": ["oil"]}, "fact_ids": ["f"], "destination_ids": ["l"], "priority": 10}],
    }


def test_valid_bundle_passes():
    validate_bundle(bundle())


def test_unknown_rule_reference_fails():
    payload = bundle()
    payload["rules"][0]["fact_ids"] = ["missing"]
    with pytest.raises(BundleError):
        validate_bundle(payload)


def test_invalid_coordinate_fails():
    payload = bundle()
    payload["locations"][0]["x"] = 9999
    with pytest.raises(BundleError):
        validate_bundle(payload)


def test_semantic_destination_rejects_fake_coordinates():
    payload = bundle()
    payload["locations"][0].update({"coordinate_status": "user-location", "x": 0, "y": 0})

    with pytest.raises(BundleError, match="must not include coordinates"):
        validate_bundle(payload)


def test_source_missing_audit_fields_fails():
    payload = bundle()
    del payload["sources"][0]["coverage"]

    with pytest.raises(BundleError, match="coverage"):
        validate_bundle(payload)


def test_rule_backed_by_unverified_fact_fails_release_gate():
    payload = bundle()
    payload["facts"][0]["confidence"] = "needs-verification"

    with pytest.raises(BundleError, match="unverified fact"):
        validate_bundle(payload)


def test_verified_coordinate_requires_named_coordinate_system():
    payload = bundle()
    del payload["locations"][0]["coordinate_system"]

    with pytest.raises(BundleError, match="coordinate system"):
        validate_bundle(payload)


def test_source_world_coordinate_cannot_claim_map_coordinates():
    payload = bundle()
    payload["locations"][0].update({
        "coordinate_status": "source-world",
        "coordinate_system": "unreal-world-v1",
        "world_x": 500000,
        "world_y": -600000,
        "world_z": 20000,
    })

    with pytest.raises(BundleError, match="must not include map coordinates"):
        validate_bundle(payload)


def test_watchtower_class_requires_map_reveal_semantics():
    payload = bundle()
    payload["locations"][0].update({"waypoint_class": "watchtower", "reveals_map": False})

    with pytest.raises(BundleError, match="map-reveal semantics"):
        validate_bundle(payload)


def test_bundled_alpha_pals_have_a_cited_first_clear_runtime_key():
    payload = load_bundle()
    alpha_pals = [item for item in payload["locations"] if item["kind"] == "alpha_pal"]

    assert len(alpha_pals) == 90
    assert all(item.get("first_clear_key") for item in alpha_pals)
    assert all(item.get("first_clear_source_id") for item in alpha_pals)
