"""Normalize the pinned PalworldSaveTools fast-travel catalog for the local bundle."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


REVISION = "e4e1439b274c1140eed5690051ce59ab14b68027"
SOURCE_ID = "palworld-save-tools-fast-travel-1.0"


def _map_round(value: float) -> int:
    """Match JavaScript Math.round used by the independently checked map transform."""
    return math.floor(value + 0.5)


def palpagos_map_coordinates(world_x: float, world_y: float) -> tuple[int, int]:
    return (_map_round((world_y - 158000) / 459), _map_round((world_x + 123888) / 459))


def world_tree_map_coordinates(world_x: float, world_y: float) -> tuple[int, int]:
    return (_map_round((world_y + 382365) / 724), _map_round((world_x + 358540) / 724))


def normalize(payload: dict[str, dict]) -> dict:
    locations = []
    for upstream_key, item in payload.items():
        upstream_id = item["id"]
        if upstream_id.startswith("WorldTree_MiddleBoss_"):
            continue
        is_watchtower = upstream_id.startswith("WatchTower_")
        record = {
            "id": f"fast-travel-{upstream_id.lower().replace('_', '-')}",
            "name": item["localized_name"],
            "kind": "fast_travel",
            "waypoint_class": "watchtower" if is_watchtower else "standard",
            "reveals_map": is_watchtower,
            "aliases": [upstream_id],
            "source_id": SOURCE_ID,
            "upstream_key": upstream_key,
            "upstream_id": upstream_id,
            "world_x": item["x"],
            "world_y": item["y"],
            "world_z": item["z"],
            "last_checked": "2026-07-14" if is_watchtower else "2026-07-13",
            "confidence": "verified",
            "verification_scope": (
                "Pinned upstream record; watchtower transfer and map-reveal behavior confirmed in Palworld 1.0."
                if is_watchtower
                else "Pinned upstream record; not independently visited in game."
            ),
        }
        if upstream_id.startswith("WorldTree_") or upstream_id.startswith("WatchTower_WorldTree_"):
            map_x, map_y = world_tree_map_coordinates(item["x"], item["y"])
            record.update({
                "coordinate_status": "verified",
                "coordinate_system": "world-tree-map-v1",
                "region": "world-tree",
                "x": map_x,
                "y": map_y,
                "guidance": "Rendered against the separately calibrated private World Tree map.",
            })
        elif upstream_id.startswith("SkyIsland_") or upstream_id == "WatchTower_22":
            record.update({
                "coordinate_status": "source-world",
                "coordinate_system": "unreal-world-v1",
                "region": "sunreach",
                "guidance": "Sunreach source coordinates are retained, but no calibrated regional map is bundled yet.",
            })
        else:
            map_x, map_y = palpagos_map_coordinates(item["x"], item["y"])
            record.update({
                "coordinate_status": "verified",
                "coordinate_system": "palpagos-map-v1",
                "region": "palpagos",
                "x": map_x,
                "y": map_y,
            })
        locations.append(record)

    if len(locations) != 171 or len({item["id"] for item in locations}) != 171:
        raise ValueError("Expected exactly 171 unique travel waypoints")
    if sum(item["waypoint_class"] == "watchtower" for item in locations) != 22:
        raise ValueError("Expected exactly 22 map-reveal watchtowers")

    return {
        "sources": [{
            "id": SOURCE_ID,
            "name": "PalworldSaveTools: fast_travel_points.json",
            "url": f"https://github.com/deafdudecomputers/PalworldSaveTools/blob/{REVISION}/resources/game_data/fast_travel_points.json",
            "coverage": [
                "fast-travel-names",
                "watchtower-names",
                "waypoint-class-identifiers",
                "unreal-world-coordinates",
                "palpagos-map-coordinate-transform",
            ],
            "reuse_status": "MIT-attribution-required; factual-coordinate subset",
            "license": "MIT",
            "attribution": "Copyright (c) 2026 Pylar",
            "pinned_revision": REVISION,
            "game_version": "1.0",
            "last_checked": "2026-07-14",
            "confidence": "verified-open-source-current",
            "contradictions": [
                "The prior audit excluded WatchTower_* records as non-transfer destinations; a Palworld 1.0 in-game check on 2026-07-14 confirmed that Windswept Island Watchtower exposes Transfer and reveals map coverage."
            ],
        }],
        "facts": [],
        "locations": locations,
        "rules": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_json", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.source_json.read_text(encoding="utf-8"))
    rendered = json.dumps(normalize(payload), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
