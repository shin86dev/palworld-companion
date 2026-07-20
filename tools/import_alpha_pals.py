"""Normalize the pinned Palworld Atlas alpha-spawn catalogs for the local bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REVISION = "1063da140f38cc80791007ab7d440fe8e121466b"
BUILD_ID = "24088465"
SOURCE_ID = f"palworld-atlas-alpha-pals-{BUILD_ID}"


def _world_tree_map_coordinates(world_x: float, world_y: float) -> tuple[float, float]:
    return ((world_y + 382365.0) / 724.0, (world_x + 358540.0) / 724.0)


def _slug(value: str) -> str:
    return value.casefold().replace("_", "-")


def normalize(catalogs: tuple[dict, ...]) -> dict:
    locations = []
    for catalog in catalogs:
        source_region = catalog.get("region")
        if source_region not in {"palpagos", "tree"}:
            raise ValueError(f"Unsupported source region: {source_region}")
        if catalog.get("coordinateSystem") != "pal-map":
            raise ValueError("Expected the pinned pal-map coordinate system")

        for item in catalog.get("spawns", []):
            if item.get("kind") != "alpha":
                continue
            region = "world-tree" if item["region"] == "tree" else "palpagos"
            if region == "world-tree":
                map_x, map_y = _world_tree_map_coordinates(item["worldX"], item["worldY"])
                coordinate_system = "world-tree-map-v1"
            else:
                map_x, map_y = item["mapX"], item["mapY"]
                coordinate_system = "palpagos-map-v1"
            name = item.get("palName") or item["palId"]
            locations.append({
                "id": f"alpha-pal-{_slug(item['id'])}",
                "name": name,
                "kind": "alpha_pal",
                "aliases": [item["palId"], item["id"], f"Alpha {name}", f"{name} Alpha"],
                "pal_id": item["palId"],
                "upstream_id": item["id"],
                "level_min": item["minLevel"],
                "level_max": item["maxLevel"],
                "availability": item["availability"],
                "region": region,
                "coordinate_status": "verified",
                "coordinate_system": coordinate_system,
                "x": map_x,
                "y": map_y,
                "world_x": item["worldX"],
                "world_y": item["worldY"],
                "source_id": SOURCE_ID,
                "last_checked": "2026-07-14",
                "confidence": "verified",
                "verification_scope": (
                    "Normalized from the pinned current-build dedicated-server spawn table; "
                    "not independently visited in game."
                ),
            })

    if len(locations) != 90 or len({item["id"] for item in locations}) != 90:
        raise ValueError("Expected exactly 90 unique Alpha Pal spawn records")
    if sum(item["region"] == "palpagos" for item in locations) != 82:
        raise ValueError("Expected exactly 82 Palpagos Alpha Pal records")
    if sum(item["region"] == "world-tree" for item in locations) != 8:
        raise ValueError("Expected exactly 8 World Tree Alpha Pal records")

    return {
        "sources": [{
            "id": SOURCE_ID,
            "name": f"Palworld Atlas Data: Alpha spawns (build {BUILD_ID})",
            "url": (
                "https://github.com/Awy64/palworld-atlas-data/tree/"
                f"{REVISION}/published/v1/builds/{BUILD_ID}/maps"
            ),
            "coverage": [
                "alpha-pal-identities",
                "alpha-pal-levels",
                "alpha-pal-regions",
                "alpha-pal-world-coordinates",
            ],
            "reuse_status": "MIT-attribution-required; normalized-factual-subset",
            "license": "MIT",
            "attribution": "Copyright (c) 2026 Adam Young",
            "pinned_revision": REVISION,
            "upstream_build_id": BUILD_ID,
            "game_version": "1.0",
            "last_checked": "2026-07-14",
            "confidence": "verified-open-source-current-build",
            "contradictions": [],
        }],
        "facts": [],
        "locations": sorted(locations, key=lambda item: (item["region"], item["name"].casefold(), item["id"])),
        "rules": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("palpagos_json", type=Path)
    parser.add_argument("tree_json", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    catalogs = tuple(
        json.loads(path.read_text(encoding="utf-8"))
        for path in (args.palpagos_json, args.tree_json)
    )
    rendered = json.dumps(normalize(catalogs), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
