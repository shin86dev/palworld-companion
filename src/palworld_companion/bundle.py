from __future__ import annotations

import json
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

from .models import BundleError

REQUIRED_FACT_FIELDS = {"id", "title", "category", "source_id", "last_checked", "confidence"}
REQUIRED_SOURCE_FIELDS = {
    "id", "name", "url", "coverage", "reuse_status", "game_version",
    "last_checked", "confidence", "contradictions",
}


def load_bundle(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        path = Path(files("palworld_companion").joinpath("data/game_data.json"))
    with path.open(encoding="utf-8") as handle:
        bundle = json.load(handle)
    for fragment_name in bundle.get("fragments", []):
        fragment_path = path.parent / fragment_name
        with fragment_path.open(encoding="utf-8") as handle:
            fragment = json.load(handle)
        for collection in ("sources", "facts", "locations", "rules"):
            bundle[collection].extend(fragment.get(collection, []))
        alpha_first_clear = fragment.get("alpha_first_clear")
        if alpha_first_clear:
            source_id = alpha_first_clear["source_id"]
            keys_by_pal_id = alpha_first_clear["keys_by_pal_id"]
            for location in bundle["locations"]:
                if location.get("kind") != "alpha_pal":
                    continue
                first_clear_key = keys_by_pal_id.get(location.get("pal_id"))
                if first_clear_key:
                    location["first_clear_key"] = first_clear_key
                    location["first_clear_source_id"] = source_id
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle: dict[str, Any]) -> None:
    for key in ("bundle_version", "game_version", "created", "sources", "facts", "locations", "rules"):
        if key not in bundle:
            raise BundleError(f"Bundle is missing required field: {key}")
    source_ids = set()
    for source in bundle["sources"]:
        missing = REQUIRED_SOURCE_FIELDS - set(source)
        if missing:
            raise BundleError(f"Source {source.get('id', '<unknown>')} missing {sorted(missing)}")
        _date(source["last_checked"], f"source {source['id']}")
        source_ids.add(source["id"])
    fact_ids = set()
    for fact in bundle["facts"]:
        missing = REQUIRED_FACT_FIELDS - set(fact)
        if missing:
            raise BundleError(f"Fact {fact.get('id', '<unknown>')} missing {sorted(missing)}")
        if fact["source_id"] not in source_ids:
            raise BundleError(f"Fact {fact['id']} refers to an unknown source")
        _date(fact["last_checked"], f"fact {fact['id']}")
        fact_ids.add(fact["id"])
    location_ids = set()
    for location in bundle["locations"]:
        if not {"id", "name", "kind", "source_id", "last_checked", "coordinate_status"}.issubset(location):
            raise BundleError("Every location needs id, name, kind, source_id, last_checked, and coordinate_status")
        if location["coordinate_status"] == "verified":
            if not location.get("coordinate_system"):
                raise BundleError(f"Location {location['id']} needs a named coordinate system")
            if not isinstance(location.get("x"), (int, float)) or not isinstance(location.get("y"), (int, float)):
                raise BundleError(f"Location {location['id']} needs numeric verified coordinates")
            if not (-2500 <= location["x"] <= 2500 and -2500 <= location["y"] <= 2500):
                raise BundleError(f"Location {location['id']} has invalid coordinates")
        elif location["coordinate_status"] == "source-world":
            if location.get("x") is not None or location.get("y") is not None:
                raise BundleError(f"Location {location['id']} must not include map coordinates for source-world status")
            if not location.get("coordinate_system"):
                raise BundleError(f"Location {location['id']} needs a named coordinate system")
            if not all(isinstance(location.get(axis), (int, float)) for axis in ("world_x", "world_y", "world_z")):
                raise BundleError(f"Location {location['id']} needs numeric source-world coordinates")
        elif location["coordinate_status"] in {"user-location", "region-only"}:
            if location.get("x") is not None or location.get("y") is not None:
                raise BundleError(f"Location {location['id']} must not include coordinates without verified coordinate status")
        else:
            raise BundleError(f"Location {location['id']} has an unsupported coordinate status")
        if location["source_id"] not in source_ids:
            raise BundleError(f"Location {location['id']} refers to an unknown source")
        waypoint_class = location.get("waypoint_class")
        if location["kind"] == "fast_travel":
            if waypoint_class not in {"standard", "watchtower"}:
                raise BundleError(f"Travel waypoint {location['id']} needs a supported waypoint class")
            if location.get("reveals_map") is not (waypoint_class == "watchtower"):
                raise BundleError(f"Travel waypoint {location['id']} has inconsistent map-reveal semantics")
        elif location["kind"] == "alpha_pal":
            required_alpha_fields = {"pal_id", "level_min", "level_max", "availability", "region"}
            missing_alpha = required_alpha_fields - set(location)
            if missing_alpha:
                raise BundleError(f"Alpha Pal {location['id']} missing {sorted(missing_alpha)}")
            if location["coordinate_status"] != "verified":
                raise BundleError(f"Alpha Pal {location['id']} needs verified coordinates")
            if location["availability"] not in {"day", "night", "both"}:
                raise BundleError(f"Alpha Pal {location['id']} has unsupported availability")
            if not all(isinstance(location[field], int) for field in ("level_min", "level_max")):
                raise BundleError(f"Alpha Pal {location['id']} needs integer levels")
            if location["level_min"] > location["level_max"]:
                raise BundleError(f"Alpha Pal {location['id']} has an invalid level range")
            if location.get("first_clear_key"):
                if location.get("first_clear_source_id") not in source_ids:
                    raise BundleError(f"Alpha Pal {location['id']} has an unknown first-clear source")
        _date(location["last_checked"], f"location {location['id']}")
        location_ids.add(location["id"])
    for rule in bundle["rules"]:
        required = {"id", "title", "action", "rationale", "when", "fact_ids", "destination_ids", "priority"}
        if not required.issubset(rule):
            raise BundleError(f"Rule {rule.get('id', '<unknown>')} is incomplete")
        unknown_facts = set(rule["fact_ids"]) - fact_ids
        unknown_locations = set(rule["destination_ids"]) - location_ids
        if unknown_facts or unknown_locations:
            raise BundleError(f"Rule {rule['id']} has unknown references")
        unverified_facts = [fact_id for fact_id in rule["fact_ids"] if next(
            fact["confidence"] for fact in bundle["facts"] if fact["id"] == fact_id
        ) != "verified"]
        if unverified_facts:
            raise BundleError(f"Rule {rule['id']} is backed by an unverified fact: {unverified_facts}")


def _date(value: str, label: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise BundleError(f"Invalid date for {label}: {value}") from error
