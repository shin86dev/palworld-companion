from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def run_first_launch_check() -> dict:
    """Exercise the zero-information path with disposable local state."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from .app import CompanionWindow
    from .map_asset import map_provision_diagnostics
    from .telemetry import probe_palworld

    checks: list[dict] = []

    def record(name: str, passed: bool, evidence) -> None:
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})

    with tempfile.TemporaryDirectory(prefix="PalPlusFirstLaunch-") as directory:
        state_path = Path(directory) / "blank-state.sqlite3"
        previous_state = os.environ.get("PALPLUS_STATE_PATH")
        os.environ["PALPLUS_STATE_PATH"] = str(state_path)
        app = QApplication.instance() or QApplication([])
        window = CompanionWindow()
        overlay = window._ensure_path_overlay()
        try:
            record(
                "blank_personal_state",
                not window.level.text()
                and window.tier.currentIndex() == -1
                and not window.mounts.text()
                and window.map_destination_id is None
                and not window.store.unlocked_ids(),
                {
                    "level": window.level.text() or None,
                    "tier": window.tier.currentText() or None,
                    "destination": window.map_destination_id,
                    "unlocked_count": len(window.store.unlocked_ids()),
                    "state_path": str(state_path),
                },
            )
            map_status = map_provision_diagnostics()
            record(
                "private_regional_map_caches_ready",
                map_status["cache_ready"]
                and map_status["tree_map"]["cache_ready"]
                and not overlay.canvas.map_image.isNull()
                and not overlay.canvas.tree_map_image.isNull(),
                {
                    "palpagos": {
                        "cache_ready": map_status["cache_ready"],
                        "cache_path": map_status["cache_path"],
                        "profile_id": map_status["profile_id"],
                    },
                    "world_tree": map_status["tree_map"],
                    "network_required": map_status["network_required"],
                },
            )
            expected_landmarks = len(window._verified_path_locations())
            travel_landmarks = [item for item in overlay.canvas.landmarks if item.get("kind") == "fast_travel"]
            alpha_landmarks = [item for item in overlay.canvas.landmarks if item.get("kind") == "alpha_pal"]
            record(
                "nearby_landmark_layer_available",
                len(overlay.canvas.landmarks) == expected_landmarks
                and len(travel_landmarks) == 164
                and len(alpha_landmarks) == 90
                and overlay.canvas.alpha_pals_visible
                and overlay.alpha_pals_toggle.isChecked(),
                {
                    "verified_travel_waypoints": len(travel_landmarks),
                    "map_reveal_watchtowers": sum(
                        item.get("waypoint_class") == "watchtower"
                        for item in travel_landmarks
                    ),
                    "alpha_pal_pois": len(alpha_landmarks),
                    "alpha_pals_default_visible": overlay.canvas.alpha_pals_visible,
                    "nearest_travel_visible_cap": 8,
                    "alpha_visible_cap": "all in active viewport",
                },
            )
            record(
                "overlay_starts_click_through",
                overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents),
                {"click_through": overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)},
            )

            window.toggle_destination_picker()
            app.processEvents()
            picker = window._ensure_destination_picker()
            record(
                "first_destination_action_opens_compact_search",
                picker.isVisible()
                and window.isHidden()
                and picker.search.hasFocus(),
                {
                    "default_key": "Delete while Palworld is foreground",
                    "fallback_key": "Ctrl+Alt+P",
                    "compact_picker_visible": picker.isVisible(),
                    "full_planner_hidden": window.isHidden(),
                    "search_has_focus": picker.search.hasFocus(),
                },
            )
            alpha_matches = picker._matching_locations("anubs")
            record(
                "alpha_pals_are_first_class_destinations",
                bool(alpha_matches)
                and alpha_matches[0].get("kind") == "alpha_pal"
                and alpha_matches[0].get("name") == "Anubis",
                {
                    "query": "anubs",
                    "first_suggestion": alpha_matches[0].get("name") if alpha_matches else None,
                    "unlock_state_required": False,
                    "layer_default_visible": overlay.alpha_pals_toggle.isChecked(),
                },
            )
            picker.set_coordinate_region("palpagos")
            picker.search.setText("-134, -95")
            coordinate_suggestion = picker.results.item(0).text() if picker.results.count() else None
            record(
                "pasted_coordinates_become_regional_destinations",
                coordinate_suggestion is not None
                and coordinate_suggestion == "⌖ -134, -95  ·  Palpagos",
                {
                    "query": "-134, -95",
                    "suggestion": coordinate_suggestion,
                    "region": picker.coordinate_region,
                    "network_required": False,
                },
            )

            picker.search.setText("Duneshleter")
            first_suggestion = picker.results.item(0).text() if picker.results.count() else None
            picker._choose_current()
            app.processEvents()
            record(
                "fuzzy_suggestion_enables_live_bearing",
                window.map_destination_id == "fast-travel-ftpoint12"
                and overlay.canvas.target is not None
                and overlay.canvas.target.get("id") == "fast-travel-ftpoint12"
                and first_suggestion == "Duneshelter",
                {
                    "query": "Duneshleter",
                    "first_suggestion": first_suggestion,
                    "destination_id": window.map_destination_id,
                    "overlay_target": overlay.canvas.target.get("name") if overlay.canvas.target else None,
                    "temporary_state_only": True,
                },
            )

            telemetry = probe_palworld(auto_audit=True)
            record(
                "live_read_path_ready_or_waiting_safely",
                telemetry.get("status") in {"live_sample_ready", "game_not_running"},
                {
                    "status": telemetry.get("status"),
                    "interpretation": (
                        "live sample verified"
                        if telemetry.get("status") == "live_sample_ready"
                        else "Palworld is closed; first launch waits without guessing"
                    ),
                    "access": telemetry.get("access"),
                    "build": telemetry.get("build"),
                },
            )
        finally:
            overlay.hide()
            overlay.cleanup()
            if window.destination_picker is not None:
                window.destination_picker.hide()
                window.destination_picker.deleteLater()
                window.destination_picker = None
            window.store.close()
            overlay.deleteLater()
            window.path_overlay = None
            window.deleteLater()
            app.processEvents()
            if previous_state is None:
                os.environ.pop("PALPLUS_STATE_PATH", None)
            else:
                os.environ["PALPLUS_STATE_PATH"] = previous_state

    return {
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "personal_state_modified": False,
        "checks": checks,
    }


def main() -> int:
    result = run_first_launch_check()
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
