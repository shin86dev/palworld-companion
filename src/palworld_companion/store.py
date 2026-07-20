from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS unlocked_fast_travel (location_id TEXT PRIMARY KEY, unlocked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS plan_history (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, check_in_json TEXT NOT NULL, result_json TEXT NOT NULL);
        """)
        self.connection.commit()

    def set_bundle_metadata(self, version: str, game_version: str) -> None:
        for key, value in {"bundle_version": version, "game_version": game_version}.items():
            self.connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
        self.connection.commit()

    def _get_meta(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
        self.connection.commit()

    def is_onboarding_complete(self) -> bool:
        return self._get_meta("onboarding_complete") == "1"

    def set_onboarding_complete(self, complete: bool) -> None:
        self._set_meta("onboarding_complete", "1" if complete else "0")

    def load_profile(self) -> dict[str, Any] | None:
        raw = self._get_meta("profile")
        if raw is None:
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Stored profile is not an object")
        return payload

    def save_profile(self, profile: dict[str, Any]) -> None:
        self._set_meta("profile", json.dumps(profile, sort_keys=True))

    def load_map_path(self) -> dict[str, str] | None:
        raw = self._get_meta("map_path")
        if raw is None:
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("start_id"), str) or not isinstance(payload.get("target_id"), str):
            raise ValueError("Stored map path is invalid")
        return {"start_id": payload["start_id"], "target_id": payload["target_id"]}

    def save_map_path(self, start_id: str, target_id: str) -> None:
        self._set_meta("map_path", json.dumps({"start_id": start_id, "target_id": target_id}, sort_keys=True))

    def clear_map_path(self) -> None:
        self.connection.execute("DELETE FROM meta WHERE key = 'map_path'")
        self.connection.commit()

    def load_map_destination(self) -> str | None:
        raw = self._get_meta("map_destination")
        if raw is not None:
            payload = json.loads(raw)
            if not isinstance(payload, str):
                raise ValueError("Stored map destination is invalid")
            return payload
        legacy = self.load_map_path()
        if legacy is None:
            return None
        destination_id = legacy["target_id"]
        self.save_map_destination(destination_id)
        self.clear_map_path()
        return destination_id

    def save_map_destination(self, location_id: str) -> None:
        self.connection.execute("DELETE FROM meta WHERE key = 'custom_map_destination'")
        self.connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('map_destination', ?)",
            (json.dumps(location_id),),
        )
        self.connection.commit()

    @staticmethod
    def _validate_custom_map_destination(payload: Any) -> dict[str, Any]:
        required = {
            "id", "name", "kind", "region", "coordinate_status", "coordinate_system",
            "x", "y", "world_x", "world_y",
        }
        if not isinstance(payload, dict) or not required.issubset(payload):
            raise ValueError("Stored coordinate destination is incomplete")
        if payload["kind"] != "map_coordinate" or payload["coordinate_status"] != "verified":
            raise ValueError("Stored coordinate destination has invalid semantics")
        if payload["region"] not in {"palpagos", "world-tree"}:
            raise ValueError("Stored coordinate destination has an unsupported region")
        if not all(isinstance(payload[key], (int, float)) and not isinstance(payload[key], bool) for key in ("x", "y", "world_x", "world_y")):
            raise ValueError("Stored coordinate destination needs numeric coordinates")
        if not all(-2500 <= float(payload[key]) <= 2500 for key in ("x", "y")):
            raise ValueError("Stored coordinate destination is outside the supported range")
        return payload

    def load_custom_map_destination(self) -> dict[str, Any] | None:
        raw = self._get_meta("custom_map_destination")
        if raw is None:
            return None
        return self._validate_custom_map_destination(json.loads(raw))

    def save_custom_map_destination(self, target: dict[str, Any]) -> None:
        target = self._validate_custom_map_destination(target)
        self.connection.execute("DELETE FROM meta WHERE key IN ('map_destination', 'map_path')")
        self.connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('custom_map_destination', ?)",
            (json.dumps(target, sort_keys=True),),
        )
        self.connection.commit()

    def clear_map_destination(self) -> None:
        self.connection.execute("DELETE FROM meta WHERE key IN ('map_destination', 'map_path', 'custom_map_destination')")
        self.connection.commit()

    def load_overlay_position(self) -> tuple[int, int] | None:
        raw = self._get_meta("overlay_position")
        if raw is None:
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("x"), int) or not isinstance(payload.get("y"), int):
            raise ValueError("Stored overlay position is invalid")
        return payload["x"], payload["y"]

    def save_overlay_position(self, x: int, y: int) -> None:
        self._set_meta("overlay_position", json.dumps({"x": int(x), "y": int(y)}, sort_keys=True))

    def clear_overlay_position(self) -> None:
        self.connection.execute("DELETE FROM meta WHERE key = 'overlay_position'")
        self.connection.commit()

    def load_overlay_anchor(self) -> tuple[float, float]:
        raw = self._get_meta("overlay_anchor")
        if raw is None:
            return 1.0, 0.0
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Stored overlay anchor is invalid")
        x_ratio = payload.get("x_ratio")
        y_ratio = payload.get("y_ratio")
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1
            for value in (x_ratio, y_ratio)
        ):
            raise ValueError("Stored overlay anchor must use normalized coordinates")
        return float(x_ratio), float(y_ratio)

    def save_overlay_anchor(self, x_ratio: float, y_ratio: float) -> None:
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1
            for value in (x_ratio, y_ratio)
        ):
            raise ValueError("Overlay anchor must use normalized coordinates")
        self._set_meta(
            "overlay_anchor",
            json.dumps(
                {"x_ratio": float(x_ratio), "y_ratio": float(y_ratio)},
                sort_keys=True,
            ),
        )

    def load_minimap_zoom(self, default: int = 40) -> int:
        raw = self._get_meta("minimap_zoom")
        if raw is None:
            return default
        payload = json.loads(raw)
        if isinstance(payload, bool) or not isinstance(payload, int) or not 0 <= payload <= 100:
            raise ValueError("Stored minimap zoom must be an integer from 0 to 100")
        return payload

    def save_minimap_zoom(self, zoom: int) -> None:
        if isinstance(zoom, bool) or not 0 <= int(zoom) <= 100:
            raise ValueError("Minimap zoom must be from 0 to 100")
        self._set_meta("minimap_zoom", json.dumps(int(zoom)))

    def _load_map_layers(self) -> dict[str, bool]:
        raw = self._get_meta("map_layers")
        if raw is None:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, bool)
            for key, value in payload.items()
        ):
            raise ValueError("Stored map layers must be a boolean object")
        return payload

    def load_map_layer_visibility(self, layer_id: str, default: bool = True) -> bool:
        if not isinstance(layer_id, str) or not layer_id:
            raise ValueError("Map layer ID must be a non-empty string")
        return self._load_map_layers().get(layer_id, bool(default))

    def save_map_layer_visibility(self, layer_id: str, visible: bool) -> None:
        if not isinstance(layer_id, str) or not layer_id:
            raise ValueError("Map layer ID must be a non-empty string")
        if not isinstance(visible, bool):
            raise ValueError("Map layer visibility must be boolean")
        layers = self._load_map_layers()
        layers[layer_id] = visible
        self._set_meta("map_layers", json.dumps(layers, sort_keys=True))

    def unlocked_ids(self) -> set[str]:
        return {row["location_id"] for row in self.connection.execute("SELECT location_id FROM unlocked_fast_travel")}

    def set_unlocked(self, location_id: str, unlocked: bool) -> None:
        if unlocked:
            self.connection.execute("INSERT OR IGNORE INTO unlocked_fast_travel(location_id) VALUES (?)", (location_id,))
        else:
            self.connection.execute("DELETE FROM unlocked_fast_travel WHERE location_id = ?", (location_id,))
        self.connection.commit()

    def save_plan(self, check_in: dict[str, Any], result: dict[str, Any]) -> None:
        self.connection.execute("INSERT INTO plan_history(check_in_json, result_json) VALUES (?, ?)", (json.dumps(check_in), json.dumps(result)))
        self.connection.commit()

    def export_state(self) -> dict[str, Any]:
        return {
            "format": 4,
            "unlocked_fast_travel": sorted(self.unlocked_ids()),
            "profile": self.load_profile(),
            "map_destination": self.load_map_destination(),
            "custom_map_destination": self.load_custom_map_destination(),
            "onboarding_complete": self.is_onboarding_complete(),
            "map_layers": self._load_map_layers(),
        }

    def import_state(self, payload: dict[str, Any], valid_location_ids: set[str]) -> None:
        if payload.get("format") not in {1, 2, 3, 4} or not isinstance(payload.get("unlocked_fast_travel"), list):
            raise ValueError("Unsupported export format")
        invalid = set(payload["unlocked_fast_travel"]) - valid_location_ids
        if invalid:
            raise ValueError(f"Import references unknown locations: {sorted(invalid)}")
        if payload["format"] == 2:
            profile = payload.get("profile")
            map_path = payload.get("map_path")
            if profile is not None and not isinstance(profile, dict):
                raise ValueError("Import profile must be an object or null")
            if map_path is not None:
                if not isinstance(map_path, dict) or not isinstance(map_path.get("start_id"), str) or not isinstance(map_path.get("target_id"), str):
                    raise ValueError("Import map path is invalid")
                invalid_path = {map_path["start_id"], map_path["target_id"]} - valid_location_ids
                if invalid_path:
                    raise ValueError(f"Import map path references unknown locations: {sorted(invalid_path)}")
        elif payload["format"] in {3, 4}:
            profile = payload.get("profile")
            map_destination = payload.get("map_destination")
            if profile is not None and not isinstance(profile, dict):
                raise ValueError("Import profile must be an object or null")
            if map_destination is not None and not isinstance(map_destination, str):
                raise ValueError("Import map destination must be a string or null")
            if map_destination is not None and map_destination not in valid_location_ids:
                raise ValueError(f"Import map destination references unknown location: {map_destination}")
            custom_map_destination = payload.get("custom_map_destination")
            if custom_map_destination is not None:
                self._validate_custom_map_destination(custom_map_destination)
            if map_destination is not None and custom_map_destination is not None:
                raise ValueError("Import cannot contain both fixed and coordinate destinations")
            map_layers = payload.get("map_layers", {})
            if payload["format"] == 4 and (
                not isinstance(map_layers, dict)
                or not all(isinstance(key, str) and isinstance(value, bool) for key, value in map_layers.items())
            ):
                raise ValueError("Import map layers must be a boolean object")
        self.connection.execute("DELETE FROM unlocked_fast_travel")
        self.connection.executemany("INSERT INTO unlocked_fast_travel(location_id) VALUES (?)", ((item,) for item in payload["unlocked_fast_travel"]))
        if payload["format"] in {2, 3, 4}:
            for key in ("profile", "map_path", "map_destination", "custom_map_destination", "onboarding_complete", "map_layers"):
                self.connection.execute("DELETE FROM meta WHERE key = ?", (key,))
            if payload.get("profile") is not None:
                self.connection.execute("INSERT INTO meta(key, value) VALUES ('profile', ?)", (json.dumps(payload["profile"], sort_keys=True),))
            if payload["format"] == 2 and payload.get("map_path") is not None:
                self.connection.execute("INSERT INTO meta(key, value) VALUES ('map_path', ?)", (json.dumps(payload["map_path"], sort_keys=True),))
            if payload["format"] in {3, 4} and payload.get("map_destination") is not None:
                self.connection.execute("INSERT INTO meta(key, value) VALUES ('map_destination', ?)", (json.dumps(payload["map_destination"]),))
            if payload["format"] in {3, 4} and payload.get("custom_map_destination") is not None:
                self.connection.execute(
                    "INSERT INTO meta(key, value) VALUES ('custom_map_destination', ?)",
                    (json.dumps(payload["custom_map_destination"], sort_keys=True),),
                )
            if payload["format"] == 4 and payload.get("map_layers"):
                self.connection.execute(
                    "INSERT INTO meta(key, value) VALUES ('map_layers', ?)",
                    (json.dumps(payload["map_layers"], sort_keys=True),),
                )
            if payload.get("onboarding_complete"):
                self.connection.execute("INSERT INTO meta(key, value) VALUES ('onboarding_complete', '1')")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
