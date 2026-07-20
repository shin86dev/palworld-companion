import math
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from palworld_companion.app import (
    CompanionWindow,
    DestinationPicker,
    MinimalOverlayFrame,
    MiniPathCanvas,
    PathOverlay,
    WindowsGameUiProbe,
    WindowsGlobalHotkey,
)
from palworld_companion.window_bind import GameWindowState, OverlayAnchor, WindowRect
import palworld_companion.app as app_module


def process_events_until(app, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def test_state_path_can_be_overridden_for_non_destructive_fresh_runs(monkeypatch, tmp_path):
    expected = tmp_path / "fresh.sqlite3"
    monkeypatch.setenv("PALPLUS_STATE_PATH", str(expected))

    assert app_module.app_data_path() == expected


def test_delete_hotkey_is_scoped_to_palworld_foreground_processes():
    assert WindowsGlobalHotkey.picker_hotkey_enabled_for_process("Palworld.exe")
    assert WindowsGlobalHotkey.picker_hotkey_enabled_for_process("PALWORLD-WIN64-SHIPPING.EXE")
    assert not WindowsGlobalHotkey.picker_hotkey_enabled_for_process("explorer.exe")
    assert not WindowsGlobalHotkey.picker_hotkey_enabled_for_process(None)


def test_minimap_interaction_requires_palworld_and_a_visible_cursor():
    assert WindowsGameUiProbe.permits_interaction("Palworld.exe", cursor_showing=True)
    assert WindowsGameUiProbe.permits_interaction("PALWORLD-WIN64-SHIPPING.EXE", cursor_showing=True)
    assert not WindowsGameUiProbe.permits_interaction("Palworld.exe", cursor_showing=False)
    assert not WindowsGameUiProbe.permits_interaction("explorer.exe", cursor_showing=True)
    assert not WindowsGameUiProbe.permits_interaction(None, cursor_showing=True)


def test_tray_has_a_visible_icon(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "app_data_path", lambda: tmp_path / "state.sqlite3")
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()

    assert not window.tray.icon().isNull()

    window.store.close()
    window.deleteLater()
    app.processEvents()


def test_blank_first_run_is_honest_and_routes_to_supported_work(monkeypatch, tmp_path):
    database = tmp_path / "state.sqlite3"
    monkeypatch.setattr(app_module, "app_data_path", lambda: database)
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()

    assert window.level.text() == ""
    assert window.tier.currentIndex() == -1
    assert window.mounts.text() == ""
    assert window.goal.text() == ""
    assert window.bottleneck.text() == ""
    assert not window.welcome_panel.isHidden()
    assert window.quick_start_button.isHidden()
    assert window.optional_context_panel.isHidden()
    assert "Local only" in window.welcome_summary.text()
    assert not hasattr(window, "map_quickstart")
    assert window.destination_quickstart.isDefault()
    assert "top-right" in window.welcome_instruction.text()
    assert "No setup required" in window.welcome_instruction.text()
    assert "live bearing" in window.welcome_instruction.text()

    window.core_quickstart.click()
    app.processEvents()

    assert window.bottleneck.text() == "Ancient Civilization Cores"
    assert window.store.is_onboarding_complete() is True
    assert window.welcome_panel.isHidden()
    assert not window.quick_start_button.isHidden()
    assert "enter your level" in window.plan_view.toPlainText().lower()

    window.store.close()
    window.deleteLater()
    app.processEvents()


def test_level_suggests_an_editable_progression_tier(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "app_data_path", lambda: tmp_path / "state.sqlite3")
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()

    for level, expected in ((1, "early"), (35, "mid"), (52, "late"), (70, "endgame"), (73, "endgame")):
        window.level.setText(str(level))
        assert window.tier.currentText() == expected

    window.store.close()
    window.deleteLater()
    app.processEvents()


def test_zero_information_destination_search_is_compact_and_fuzzy(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "app_data_path", lambda: tmp_path / "state.sqlite3")
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()

    window.toggle_destination_picker()
    app.processEvents()

    picker = window._ensure_destination_picker()
    assert window.isHidden()
    assert picker.isVisible()
    assert picker.search.hasFocus()
    assert window.store.is_onboarding_complete() is False

    picker.search.setText("Duneshleter")
    assert picker.results.item(0).text() == "Duneshelter"
    picker._choose_current()

    assert window.map_destination_id == "fast-travel-ftpoint12"
    assert window.store.is_onboarding_complete() is True
    assert "live bearing" in window.path_status.text()
    assert picker.isHidden()
    overlay = window._ensure_path_overlay()
    overlay.hide()
    window.store.close()
    picker.deleteLater()
    window.destination_picker = None
    overlay.deleteLater()
    window.path_overlay = None
    window.deleteLater()
    app.processEvents()


def test_completed_welcome_stays_hidden_but_can_be_reopened(monkeypatch, tmp_path):
    database = tmp_path / "state.sqlite3"
    monkeypatch.setattr(app_module, "app_data_path", lambda: database)
    app = QApplication.instance() or QApplication([])
    first = CompanionWindow()
    first._finish_onboarding()
    first.store.close()
    first.deleteLater()
    app.processEvents()

    reopened = CompanionWindow()
    assert reopened.welcome_panel.isHidden()

    reopened.quick_start_button.click()
    assert not reopened.welcome_panel.isHidden()

    reopened.store.close()
    reopened.deleteLater()
    app.processEvents()


def test_got_it_dismisses_window_without_hiding_live_minimap(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "app_data_path", lambda: tmp_path / "state.sqlite3")
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()
    window.show()
    window._show_path_overlay()
    overlay = window._ensure_path_overlay()

    window.skip_welcome.click()

    assert window.isHidden()
    assert overlay.isVisible()
    assert window.store.is_onboarding_complete() is True
    overlay.hide()
    window.store.close()
    overlay.deleteLater()
    window.path_overlay = None
    window.deleteLater()
    app.processEvents()


def test_core_quickstart_reaches_a_cited_plan_from_level_73(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "app_data_path", lambda: tmp_path / "state.sqlite3")
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()

    window.core_quickstart.click()
    window.level.setText("73")
    window.generate_plan()

    rendered = window.plan_view.toPlainText()
    assert "Primary:" in rendered
    assert "Evidence:" in rendered
    assert window.active_destination_ids
    assert window.store.load_profile()["level"] == 73

    window.store.close()
    window.deleteLater()
    app.processEvents()


def test_search_quickstart_reaches_local_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "app_data_path", lambda: tmp_path / "state.sqlite3")
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()

    window.search_quickstart.click()
    window.search_box.setText("ACC farm")
    window.search_local_knowledge()

    assert "Local knowledge" in window.plan_view.toPlainText()
    assert "Core" in window.plan_view.toPlainText()

    window.store.close()
    window.deleteLater()
    app.processEvents()


def test_fast_travel_unlock_toggle_needs_no_minimap_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "app_data_path", lambda: tmp_path / "state.sqlite3")
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()

    window.fast_travel_filter.setText("Mount Obsidian - Midpoint")
    app.processEvents()

    assert window.fast_travel_layout.count() == 1
    checkbox = window.fast_travel_layout.itemAt(0).widget()
    assert checkbox.text() == "Mount Obsidian - Midpoint"
    checkbox.setChecked(True)
    assert "Mount Obsidian - Midpoint" in window.map_view.last_html

    window.store.close()
    window.deleteLater()
    app.processEvents()


def test_optional_destination_persists_without_manual_origin(monkeypatch, tmp_path):
    database = tmp_path / "state.sqlite3"
    monkeypatch.setattr(app_module, "app_data_path", lambda: database)
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()

    assert not hasattr(window, "path_start")
    window.map_destination.setCurrentText("Duneshelter")
    window._set_map_destination()

    assert window.map_destination_id == "fast-travel-ftpoint12"
    assert "Duneshelter" in window.path_status.text()
    assert "live bearing" in window.path_status.text()
    assert "Duneshelter" in window.map_view.last_html
    assert "<line" not in window.map_view.last_html
    assert window.overlay_button.isEnabled()
    assert window.store.load_map_destination() == window.map_destination_id

    overlay = window._ensure_path_overlay()
    assert overlay.destination_label.text() == "Duneshelter"
    assert "Waiting for live bearing" in overlay.direction_label.text()
    assert "Connecting live read-only minimap" in overlay.disclaimer_label.text()
    assert overlay.isVisible()
    window._toggle_path_overlay()
    assert overlay.isHidden()
    window._toggle_path_overlay()
    app.processEvents()
    assert overlay.isVisible()
    overlay.hide()

    window.store.close()
    overlay.deleteLater()
    window.path_overlay = None
    window.deleteLater()
    app.processEvents()

    reopened = CompanionWindow()
    assert reopened.map_destination.currentText() == "Duneshelter"
    assert reopened.store.load_map_destination() == "fast-travel-ftpoint12"
    assert "Duneshelter" in reopened.map_view.last_html

    reopened.store.close()
    if reopened.path_overlay is not None:
        reopened.path_overlay.deleteLater()
    reopened.deleteLater()
    app.processEvents()


def test_zero_information_minimap_opens_without_destination(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "app_data_path", lambda: tmp_path / "state.sqlite3")
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()

    window._show_path_overlay()
    overlay = window._ensure_path_overlay()

    assert overlay.isVisible()
    assert overlay.destination_label.text() == ""
    assert overlay.destination_label.isHidden()
    assert overlay.canvas.width() >= 330
    assert overlay.canvas.height() >= 230
    assert len(overlay.canvas.landmarks) == len(window._verified_path_locations()) == 254
    assert overlay.canvas.alpha_pals_visible is True
    assert window.map_destination.currentIndex() == -1
    assert window.store.load_map_destination() is None

    overlay.hide()
    window.store.close()
    overlay.deleteLater()
    window.path_overlay = None
    window.deleteLater()
    app.processEvents()


def test_live_overlay_defaults_to_click_through_play_mode():
    app = QApplication.instance() or QApplication([])
    overlay = PathOverlay()

    class Reader:
        def sample(self):
            return {
                "position": {"x": 3109.94, "y": -118784.95, "z": 3688.39},
                "heading_degrees": 28.62,
            }

    overlay.live_reader = Reader()
    overlay._poll_live_telemetry()

    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert "Alt-drag" not in overlay.disclaimer_label.text()
    assert "Del choose destination" in overlay.disclaimer_label.text()
    assert "Tab zoom" in overlay.disclaimer_label.text()
    assert overlay.zoom_panel.isHidden()
    overlay.live_reader = None
    overlay.deleteLater()
    app.processEvents()


def test_destination_search_accepts_pasted_palpagos_coordinates():
    app = QApplication.instance() or QApplication([])
    selected = []
    picker = DestinationPicker((), selected.append, lambda: None)
    picker.set_coordinate_region("palpagos")

    picker.search.setText("-134, -95")

    assert picker.results.count() == 1
    assert picker.results.item(0).text() == "⌖ -134, -95  ·  Palpagos"
    picker._choose_current()
    target = selected[0]
    assert target["kind"] == "map_coordinate"
    assert target["region"] == "palpagos"
    assert (target["x"], target["y"]) == (-134, -95)
    restored = MiniPathCanvas.unreal_to_legacy(target["world_x"], target["world_y"])
    assert restored.x() == pytest.approx(-134)
    assert restored.y() == pytest.approx(-95)
    picker.deleteLater()
    app.processEvents()


def test_destination_search_uses_world_tree_coordinate_transform_when_active():
    app = QApplication.instance() or QApplication([])
    selected = []
    picker = DestinationPicker((), selected.append, lambda: None)
    picker.set_coordinate_region("world-tree")

    picker.search.setText("x=-1500 y=1512")
    picker._choose_current()

    target = selected[0]
    assert target["region"] == "world-tree"
    restored = MiniPathCanvas.world_tree_map_coordinates(target["world_x"], target["world_y"])
    assert restored.x() == pytest.approx(-1500)
    assert restored.y() == pytest.approx(1512)
    picker.deleteLater()
    app.processEvents()


def test_destination_search_rejects_out_of_range_coordinates():
    app = QApplication.instance() or QApplication([])
    picker = DestinationPicker((), lambda _target: None, lambda: None)
    picker.set_coordinate_region("palpagos")

    picker.search.setText("9000, -95")

    assert picker.results.item(0).text() == "Coordinates must be between -2500 and 2500"
    assert not (picker.results.item(0).flags() & Qt.ItemFlag.ItemIsEnabled)
    picker.deleteLater()
    app.processEvents()


def test_coordinate_destination_routes_and_persists_through_companion(monkeypatch, tmp_path):
    database = tmp_path / "state.sqlite3"
    monkeypatch.setattr(app_module, "app_data_path", lambda: database)
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()
    overlay = window._ensure_path_overlay()
    overlay.canvas.active_region = "palpagos"

    window.toggle_destination_picker()
    picker = window._ensure_destination_picker()
    picker.search.setText("-134, -95")
    picker._choose_current()

    assert window.map_destination_target["kind"] == "map_coordinate"
    assert overlay.canvas.target == window.map_destination_target
    assert window.store.load_custom_map_destination() == window.map_destination_target
    assert "Coordinates (-134, -95)" in window.path_status.text()

    overlay.hide()
    window.store.close()
    picker.deleteLater()
    window.destination_picker = None
    overlay.deleteLater()
    window.path_overlay = None
    window.deleteLater()
    app.processEvents()

    reopened = CompanionWindow()
    assert reopened.map_destination_target["kind"] == "map_coordinate"
    assert (reopened.map_destination_target["x"], reopened.map_destination_target["y"]) == (-134, -95)
    reopened.store.close()
    reopened.deleteLater()
    app.processEvents()


def test_pause_menu_cursor_exposes_zoom_then_restores_click_through():
    app = QApplication.instance() or QApplication([])
    cursor_available = False
    overlay = PathOverlay(interaction_probe=lambda: cursor_available)

    overlay._refresh_interaction_state()
    assert overlay.interaction_enabled is False
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert overlay.zoom_panel.isHidden()

    cursor_available = True
    overlay._refresh_interaction_state()
    assert overlay.interaction_enabled is True
    assert not overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert not overlay.zoom_panel.isHidden()
    assert "wheel / slider zoom" in overlay.disclaimer_label.text()

    cursor_available = False
    overlay._refresh_interaction_state()
    assert overlay.interaction_enabled is False
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert overlay.zoom_panel.isHidden()
    overlay.deleteLater()
    app.processEvents()


def test_minimap_zoom_slider_and_wheel_path_share_one_zoom_state():
    app = QApplication.instance() or QApplication([])
    overlay = PathOverlay(initial_zoom=40, interaction_probe=lambda: True)
    emitted: list[int] = []
    overlay.zoom_changed.connect(emitted.append)
    initial_crop_width = overlay.canvas.crop_width

    overlay.zoom_slider.setValue(65)

    assert overlay.canvas.zoom == 65
    assert overlay.canvas.crop_width < initial_crop_width
    assert emitted[-1] == 65

    overlay.canvas.adjust_zoom(-2)

    assert overlay.canvas.zoom == 55
    assert overlay.zoom_slider.value() == 55
    assert emitted[-1] == 55
    overlay.deleteLater()
    app.processEvents()


def test_selected_minimal_frame_draws_azure_keyline_corners_and_notch():
    app = QApplication.instance() or QApplication([])
    frame = MinimalOverlayFrame()
    frame.resize(340, 240)
    image = QImage(frame.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("transparent"))

    frame.render(image)

    signal_pixels = sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).blue() > 180
        and image.pixelColor(x, y).blue() > image.pixelColor(x, y).red() * 1.5
    )
    assert signal_pixels > 400
    frame.deleteLater()
    app.processEvents()


def test_overlay_prepares_private_map_in_background_without_setup_button(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "map.png"
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(QColor("#28405a"))
    assert image.save(str(image_path))
    monkeypatch.setattr(app_module, "local_map_image_path", lambda: None)
    monkeypatch.setattr(app_module, "local_tree_map_image_path", lambda: None)
    monkeypatch.setattr(app_module, "automatic_map_provision_needed", lambda: True)
    monkeypatch.setattr(app_module, "provision_local_maps", lambda: {"palpagos": str(image_path)})
    overlay = PathOverlay()

    overlay._start_map_provision()

    assert overlay.map_provision_state == "preparing"
    assert "Preparing private map" in overlay.disclaimer_label.text()
    assert process_events_until(app, lambda: overlay.map_provision_state == "ready")
    assert not overlay.canvas.map_image.isNull()
    assert "local map" in overlay.disclaimer_label.text()
    overlay.cleanup()
    overlay.deleteLater()
    app.processEvents()


def test_overlay_keeps_grid_and_exposes_map_provision_failure(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app_module, "local_map_image_path", lambda: None)
    monkeypatch.setattr(app_module, "local_tree_map_image_path", lambda: None)
    monkeypatch.setattr(app_module, "automatic_map_provision_needed", lambda: True)

    def fail():
        raise RuntimeError("installed archive profile changed")

    monkeypatch.setattr(app_module, "provision_local_maps", fail)
    overlay = PathOverlay()

    overlay._start_map_provision()

    assert process_events_until(app, lambda: overlay.map_provision_state == "failed")
    assert overlay.canvas.map_image.isNull()
    assert "Map setup paused" in overlay.disclaimer_label.text()
    assert "installed archive profile changed" in overlay.disclaimer_label.toolTip()
    overlay.cleanup()
    overlay.deleteLater()
    app.processEvents()


def test_overlay_waits_and_reconnects_when_palworld_starts(monkeypatch):
    app = QApplication.instance() or QApplication([])
    overlay = PathOverlay()

    def game_absent():
        raise RuntimeError("Palworld is not running.")

    monkeypatch.setattr(overlay, "_open_live_reader", game_absent)
    overlay._start_live_reader()

    assert overlay.live_reader is None
    assert overlay.next_live_connect_at > 0
    assert "Waiting for Palworld" in overlay.disclaimer_label.text()

    class Reader:
        def sample(self):
            return {
                "position": {"x": 3109.94, "y": -118784.95, "z": 3688.39},
                "heading_degrees": 28.62,
            }

        def close(self):
            pass

    monkeypatch.setattr(overlay, "_open_live_reader", Reader)
    overlay.next_live_connect_at = 0
    overlay._poll_live_telemetry()

    assert isinstance(overlay.live_reader, Reader)
    assert overlay.canvas.live_sample is not None
    assert overlay.live_error is None
    overlay.cleanup()
    overlay.deleteLater()
    app.processEvents()


def test_overlay_exposes_local_build_audit_progress_and_review_state():
    app = QApplication.instance() or QApplication([])
    overlay = PathOverlay()

    overlay._live_audit_status_changed("validating")

    assert "validating locally" in overlay.disclaimer_label.text()

    overlay._connection_failed(RuntimeError(
        "Unsupported Palworld executable fingerprint: TEST; "
        "local auto-audit failed: no unique GWorld candidate"
    ))

    assert "update needs review" in overlay.disclaimer_label.text()
    assert "audit report saved" in overlay.disclaimer_label.text()
    overlay.cleanup()
    overlay.deleteLater()
    app.processEvents()


def test_overlay_preserves_last_validated_sample_during_live_reader_pause(monkeypatch):
    app = QApplication.instance() or QApplication([])
    overlay = PathOverlay()

    class BrokenReader:
        def __init__(self):
            self.closed = False

        def sample(self):
            raise RuntimeError("ReadProcessMemory failed because the game exited")

        def close(self):
            self.closed = True

    reader = BrokenReader()
    overlay.live_reader = reader
    last_sample = {"position": {"x": 1, "y": 2, "z": 3}, "heading_degrees": 4}
    overlay.canvas.set_live_sample(last_sample)
    overlay._poll_live_telemetry()

    assert reader.closed is True
    assert overlay.live_reader is None
    assert overlay.canvas.live_sample == last_sample
    assert overlay.next_live_connect_at > 0
    assert "Live read paused" in overlay.disclaimer_label.text()
    assert "retrying" in overlay.disclaimer_label.text()

    resumed_sample = {"position": {"x": 5, "y": 6, "z": 7}, "heading_degrees": 8}

    class RecoveredReader:
        def sample(self):
            return resumed_sample

        def close(self):
            pass

    monkeypatch.setattr(overlay, "_open_live_reader", RecoveredReader)
    overlay.next_live_connect_at = 0
    overlay._poll_live_telemetry()

    assert overlay.canvas.live_sample == resumed_sample
    assert overlay.live_status == "Read-only"
    assert "Live read paused" not in overlay.disclaimer_label.text()
    overlay.deleteLater()
    app.processEvents()


def test_live_map_coordinate_transforms_round_trip():
    unreal_x, unreal_y = -749631.48, -293406.54
    legacy = MiniPathCanvas.unreal_to_legacy(unreal_x, unreal_y)
    restored = MiniPathCanvas.legacy_to_unreal(legacy.x(), legacy.y())

    assert restored.x() == pytest.approx(unreal_x)
    assert restored.y() == pytest.approx(unreal_y)
    image = MiniPathCanvas.unreal_to_base_image(unreal_x, unreal_y)
    assert 0 <= image.x() <= MiniPathCanvas.MAP_SIZE
    assert 0 <= image.y() <= MiniPathCanvas.MAP_SIZE


def test_world_tree_waypoints_use_the_separate_tree_map_transform():
    world_tree = next(
        item for item in app_module.load_bundle()["locations"]
        if item["name"] == "The Verdant Rootpath"
    )

    assert MiniPathCanvas.region_for_unreal_position(
        world_tree["world_x"], world_tree["world_y"]
    ) == "world-tree"
    image = MiniPathCanvas.world_tree_unreal_to_base_image(
        world_tree["world_x"], world_tree["world_y"]
    )
    assert 0 <= image.x() <= MiniPathCanvas.MAP_SIZE
    assert 0 <= image.y() <= MiniPathCanvas.MAP_SIZE


def test_minimap_renders_player_and_route_cues():
    app = QApplication.instance() or QApplication([])
    canvas = MiniPathCanvas()
    canvas.resize(340, 240)
    position = {"x": 2945.72, "y": -118944.32, "z": 3038.4}
    current = canvas.unreal_to_legacy(position["x"], position["y"])
    canvas.set_destination({"x": current.x() + 500, "y": current.y(), "name": "Target"})
    canvas.set_live_sample({"position": position, "heading_degrees": 183.0})

    image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("transparent"))
    canvas.render(image)

    colors = [image.pixelColor(x, y).name() for y in range(image.height()) for x in range(image.width())]
    assert colors.count("#ffd21f") > 10
    assert colors.count("#ffb454") > 10
    canvas.deleteLater()
    app.processEvents()


def test_minimap_renders_default_unlocked_and_target_landmarks():
    app = QApplication.instance() or QApplication([])
    canvas = MiniPathCanvas()
    canvas.resize(340, 240)
    position = {"x": -34446.0, "y": 571757.0, "z": -730.0}
    landmarks = (
        {"id": "default", "name": "Default", "world_x": position["x"], "world_y": position["y"] + 35_000},
        {"id": "unlocked", "name": "Unlocked", "world_x": position["x"], "world_y": position["y"] - 35_000},
        {"id": "target", "name": "Target", "world_x": position["x"] + 35_000, "world_y": position["y"]},
    )
    canvas.set_landmarks(landmarks, {"unlocked"})
    canvas.set_destination(landmarks[2])
    canvas.set_live_sample({"position": position, "heading_degrees": 0.0})

    image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("transparent"))
    canvas.render(image)

    colors = [image.pixelColor(x, y).name() for y in range(image.height()) for x in range(image.width())]
    assert colors.count("#71808f") > 0
    assert colors.count("#52c9ff") > 0
    assert colors.count("#ffb454") > 0
    canvas.deleteLater()
    app.processEvents()


def test_minimap_dispatches_watchtowers_to_a_distinct_higher_tier_glyph():
    app = QApplication.instance() or QApplication([])

    class RecordingCanvas(MiniPathCanvas):
        def __init__(self):
            super().__init__()
            self.standard_glyphs = 0
            self.watchtower_glyphs = 0

        def _draw_waypoint_glyph(self, *args, **kwargs):
            self.standard_glyphs += 1

        def _draw_watchtower_glyph(self, *args, **kwargs):
            self.watchtower_glyphs += 1

    canvas = RecordingCanvas()
    canvas.resize(340, 240)
    position = {"x": -348065.06, "y": 151660.97, "z": 1200.0}
    canvas.set_landmarks((
        {
            "id": "standard",
            "name": "Standard waypoint",
            "world_x": position["x"],
            "world_y": position["y"] + 10_000,
            "waypoint_class": "standard",
        },
        {
            "id": "watchtower",
            "name": "Windswept Island Watchtower",
            "world_x": position["x"],
            "world_y": position["y"] - 10_000,
            "waypoint_class": "watchtower",
            "reveals_map": True,
        },
    ))
    canvas.set_live_sample({"position": position, "heading_degrees": 0.0})
    image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("transparent"))

    canvas.render(image)

    assert canvas.standard_glyphs == 1
    assert canvas.watchtower_glyphs == 1
    canvas.deleteLater()
    app.processEvents()


def test_tray_exposes_explicit_quit_without_toggling_on_context_click(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("PALPLUS_STATE_PATH", str(tmp_path / "tray.sqlite3"))

    class TrayWindow(CompanionWindow):
        def __init__(self):
            self.quit_requests = 0
            self.toggle_requests = 0
            super().__init__()

        def _quit_from_tray(self):
            self.quit_requests += 1

        def toggle_visible(self):
            self.toggle_requests += 1

    window = TrayWindow()
    menu = window.tray.contextMenu()
    actions = {action.text(): action for action in menu.actions() if not action.isSeparator()}

    assert "Quit PalPlus" in actions
    actions["Quit PalPlus"].trigger()
    assert window.quit_requests == 1
    window._tray_activated(QSystemTrayIcon.ActivationReason.Context)
    assert window.toggle_requests == 0

    window.tray.hide()
    window.store.close()
    window.deleteLater()
    app.processEvents()


def test_minimap_draws_live_check_and_x_states_inside_waypoints():
    app = QApplication.instance() or QApplication([])

    class RecordingCanvas(MiniPathCanvas):
        def __init__(self):
            super().__init__()
            self.state_marks = []

        def _draw_waypoint_state_mark(self, _painter, _point, *, unlocked):
            self.state_marks.append(unlocked)

    unlocked_key = "596996B948716D3FD2283C8B5C6E829C"
    locked_key = "4C204C3842EAB210A7A9DA9D2CF9CBBE"
    canvas = RecordingCanvas()
    canvas.resize(340, 240)
    position = {"x": -348065.06, "y": 151660.97, "z": 1200.0}
    canvas.set_landmarks((
        {
            "id": "unlocked",
            "name": "Unlocked",
            "kind": "fast_travel",
            "upstream_key": unlocked_key,
            "world_x": position["x"],
            "world_y": position["y"] + 10_000,
        },
        {
            "id": "locked",
            "name": "Locked",
            "kind": "fast_travel",
            "upstream_key": locked_key,
            "world_x": position["x"],
            "world_y": position["y"] - 10_000,
        },
    ))
    canvas.set_live_unlocked_waypoint_keys({unlocked_key})
    canvas.set_live_sample({"position": position, "heading_degrees": 0.0})
    image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("transparent"))

    canvas.render(image)

    assert sorted(canvas.state_marks) == [False, True]
    canvas.deleteLater()
    app.processEvents()


def test_waypoint_check_icon_has_a_legible_state_footprint():
    app = QApplication.instance() or QApplication([])
    canvas = MiniPathCanvas()
    image = QImage(48, 48, QImage.Format.Format_ARGB32)
    image.fill(QColor("transparent"))
    painter = QPainter(image)

    canvas._draw_waypoint_state_mark(painter, QPointF(14, 14), unlocked=True)
    painter.end()

    pixels = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 180
    ]
    assert pixels
    assert max(x for x, _y in pixels) - min(x for x, _y in pixels) + 1 >= 11
    assert max(y for _x, y in pixels) - min(y for _x, y in pixels) + 1 >= 10
    canvas.deleteLater()
    app.processEvents()


def test_far_zoom_culls_overlapping_travel_state_markers():
    app = QApplication.instance() or QApplication([])

    class RecordingCanvas(MiniPathCanvas):
        def __init__(self):
            super().__init__()
            self.travel_points = []

        def _actual_image_point(self, point, image=None):
            return point / 2

        def _draw_waypoint_glyph(self, _painter, point, **_kwargs):
            self.travel_points.append((point.x(), point.y()))

        def _draw_watchtower_glyph(self, _painter, point, **_kwargs):
            self.travel_points.append((point.x(), point.y()))

    canvas = RecordingCanvas()
    canvas.resize(340, 240)
    locations = tuple(
        item for item in app_module.load_bundle()["locations"]
        if item.get("kind") == "fast_travel"
        and item.get("coordinate_status") == "verified"
    )
    unlocked_keys = {
        str(item.get("upstream_key", "")).upper()
        for item in locations
        if item.get("upstream_key")
    }
    canvas.set_landmarks(locations)
    canvas.set_live_unlocked_waypoint_keys(unlocked_keys)
    canvas.set_zoom(canvas.MIN_ZOOM)
    canvas.set_live_sample({
        "position": {
            "x": 500649.15570112545,
            "y": -750869.4942812478,
            "z": 41577.51488929615,
        },
        "heading_degrees": 230.0,
    })
    image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("transparent"))

    canvas.render(image)

    distances = [
        math.dist(first, second)
        for index, first in enumerate(canvas.travel_points)
        for second in canvas.travel_points[index + 1:]
    ]
    assert distances
    assert min(distances) >= 22
    canvas.deleteLater()
    app.processEvents()


def test_minimap_omits_check_or_x_when_live_unlock_state_is_unknown():
    app = QApplication.instance() or QApplication([])

    class RecordingCanvas(MiniPathCanvas):
        def __init__(self):
            super().__init__()
            self.state_marks = 0

        def _draw_waypoint_state_mark(self, *_args, **_kwargs):
            self.state_marks += 1

    canvas = RecordingCanvas()
    canvas.resize(340, 240)
    position = {"x": -348065.06, "y": 151660.97, "z": 1200.0}
    canvas.set_landmarks(({
        "id": "unknown",
        "name": "Unknown",
        "kind": "fast_travel",
        "upstream_key": "596996B948716D3FD2283C8B5C6E829C",
        "world_x": position["x"],
        "world_y": position["y"] + 10_000,
    },))
    canvas.set_live_sample({"position": position, "heading_degrees": 0.0})
    image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("transparent"))

    canvas.render(image)

    assert canvas.state_marks == 0
    canvas.deleteLater()
    app.processEvents()


def test_cave_associated_fast_travel_uses_waypoint_not_tower_glyph():
    app = QApplication.instance() or QApplication([])

    class RecordingCanvas(MiniPathCanvas):
        def __init__(self):
            super().__init__()
            self.waypoint_glyphs = 0
            self.watchtower_glyphs = 0

        def _draw_waypoint_glyph(self, *args, **kwargs):
            self.waypoint_glyphs += 1

        def _draw_watchtower_glyph(self, *args, **kwargs):
            self.watchtower_glyphs += 1

    cave_waypoint = next(
        item for item in app_module.load_bundle()["locations"]
        if item["name"] == "Stone Pillar Cave Entrance"
    )
    canvas = RecordingCanvas()
    canvas.resize(340, 240)
    position = {
        "x": cave_waypoint["world_x"],
        "y": cave_waypoint["world_y"] - 10_000,
        "z": cave_waypoint["world_z"],
    }
    canvas.set_landmarks((cave_waypoint,))
    canvas.set_live_sample({"position": position, "heading_degrees": 0.0})
    image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("transparent"))

    canvas.render(image)

    assert cave_waypoint["waypoint_class"] == "standard"
    assert canvas.waypoint_glyphs == 1
    assert canvas.watchtower_glyphs == 0
    canvas.deleteLater()
    app.processEvents()


def test_destination_picker_decorates_map_reveal_watchtower_suggestions():
    app = QApplication.instance() or QApplication([])
    watchtower = {
        "id": "fast-travel-watchtower-1",
        "name": "Windswept Island Watchtower",
        "aliases": ["WatchTower_1"],
        "waypoint_class": "watchtower",
        "reveals_map": True,
    }
    picker = DestinationPicker((watchtower,), lambda _target: None, lambda: None)

    picker.search.setText("windswept watch")

    assert picker.results.count() == 1
    assert picker.results.item(0).text() == "◆ Windswept Island Watchtower"
    assert "reveals map" in picker.results.item(0).toolTip().lower()
    picker.deleteLater()
    app.processEvents()


def test_destination_picker_prioritizes_and_decorates_live_waypoint_state():
    app = QApplication.instance() or QApplication([])
    locked_key = "4C204C3842EAB210A7A9DA9D2CF9CBBE"
    unlocked_key = "596996B948716D3FD2283C8B5C6E829C"
    picker = DestinationPicker((
        {
            "id": "locked",
            "name": "Cove East",
            "kind": "fast_travel",
            "aliases": [],
            "upstream_key": locked_key,
        },
        {
            "id": "unlocked",
            "name": "Cove West",
            "kind": "fast_travel",
            "aliases": [],
            "upstream_key": unlocked_key,
        },
    ), lambda _target: None, lambda: None)

    picker.set_live_unlocked_waypoint_keys({unlocked_key})
    picker.search.setText("cove")

    assert picker.results.item(0).text().startswith("✓ Cove West")
    assert picker.results.item(1).text().startswith("× Cove East")
    assert "runtime-confirmed unlocked" in picker.results.item(0).toolTip().lower()
    assert "runtime-confirmed locked" in picker.results.item(1).toolTip().lower()
    picker.deleteLater()
    app.processEvents()


def test_destination_picker_decorates_alpha_pal_suggestions():
    app = QApplication.instance() or QApplication([])
    alpha = {
        "id": "alpha-pal-alpha-46-anubis",
        "name": "Anubis",
        "aliases": ["Anubis"],
        "kind": "alpha_pal",
        "level_min": 55,
        "level_max": 55,
    }
    picker = DestinationPicker((alpha,), lambda _target: None, lambda: None)

    picker.search.setText("anubs")

    assert picker.results.count() == 1
    assert picker.results.item(0).text() == "★ Anubis  ·  Lv 55"
    assert "always available" in picker.results.item(0).toolTip().lower()
    picker.deleteLater()
    app.processEvents()


def test_destination_picker_decorates_live_alpha_first_clear_state():
    app = QApplication.instance() or QApplication([])
    alpha = {
        "id": "alpha-pal-alpha-46-anubis",
        "name": "Anubis",
        "aliases": ["Anubis"],
        "kind": "alpha_pal",
        "level_min": 55,
        "level_max": 55,
        "first_clear_key": "81_1_grass_FBOSS_14",
    }
    picker = DestinationPicker((alpha,), lambda _target: None, lambda: None)

    picker.set_live_cleared_alpha_keys({"81_1_GRASS_FBOSS_14"})
    picker.search.setText("anubis")

    assert picker.results.item(0).text().startswith("✓ ★ Anubis")
    assert "first clear confirmed" in picker.results.item(0).toolTip().lower()

    picker.set_live_cleared_alpha_keys(set())
    assert picker.results.item(0).text().startswith("1ST ★ Anubis")
    assert "still available" in picker.results.item(0).toolTip().lower()
    picker.deleteLater()
    app.processEvents()


def test_minimap_draws_alpha_pals_above_travel_waypoints_and_can_hide_layer():
    app = QApplication.instance() or QApplication([])

    class RecordingCanvas(MiniPathCanvas):
        def __init__(self):
            super().__init__()
            self.glyph_order = []
            self.alpha_labels = []

        def _draw_waypoint_glyph(self, *args, **kwargs):
            self.glyph_order.append("travel")

        def _draw_alpha_pal_glyph(self, *args, **kwargs):
            self.glyph_order.append("alpha")

        def _draw_alpha_pal_label(self, _painter, _point, landmark, **_kwargs):
            self.alpha_labels.append(self._alpha_pal_short_label(landmark))

    canvas = RecordingCanvas()
    canvas.resize(340, 240)
    position = {"x": -348065.06, "y": 151660.97, "z": 1200.0}
    canvas.set_landmarks((
        {
            "id": "travel",
            "name": "Travel",
            "kind": "fast_travel",
            "world_x": position["x"],
            "world_y": position["y"] + 10_000,
            "waypoint_class": "standard",
        },
        {
            "id": "alpha",
            "name": "Anubis",
            "kind": "alpha_pal",
            "level_min": 55,
            "level_max": 55,
            "world_x": position["x"],
            "world_y": position["y"] - 10_000,
        },
    ))
    canvas.set_live_sample({"position": position, "heading_degrees": 0.0})
    image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("transparent"))

    canvas.render(image)
    assert canvas.glyph_order == ["travel", "alpha"]
    assert canvas.alpha_labels == ["ANUB · 55"]

    canvas.glyph_order.clear()
    canvas.alpha_labels.clear()
    canvas.set_alpha_pals_visible(False)
    canvas.render(image)
    assert canvas.glyph_order == ["travel"]
    assert canvas.alpha_labels == []
    canvas.deleteLater()
    app.processEvents()


def test_minimap_passes_live_first_clear_state_to_alpha_rendering():
    app = QApplication.instance() or QApplication([])

    class RecordingCanvas(MiniPathCanvas):
        def __init__(self):
            super().__init__()
            self.clear_states = []

        def _draw_alpha_pal_glyph(self, _painter, _point, *, cleared, **_kwargs):
            self.clear_states.append(cleared)

        def _draw_alpha_pal_label(self, *_args, **_kwargs):
            pass

    canvas = RecordingCanvas()
    canvas.resize(340, 240)
    position = {"x": -348065.06, "y": 151660.97, "z": 1200.0}
    canvas.set_landmarks(({
        "id": "alpha",
        "name": "Anubis",
        "kind": "alpha_pal",
        "level_min": 55,
        "level_max": 55,
        "first_clear_key": "81_1_grass_FBOSS_14",
        "world_x": position["x"],
        "world_y": position["y"] - 10_000,
    },))
    canvas.set_live_cleared_alpha_keys({"81_1_GRASS_FBOSS_14"})
    canvas.set_live_sample({"position": position, "heading_degrees": 0.0})
    image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("transparent"))

    canvas.render(image)

    assert canvas.clear_states == [True]
    canvas.deleteLater()
    app.processEvents()


def test_alpha_pal_labels_use_a_compact_name_prefix_and_level():
    assert MiniPathCanvas._alpha_pal_short_label({
        "name": "Anubis", "level_min": 55, "level_max": 55,
    }) == "ANUB · 55"
    assert MiniPathCanvas._alpha_pal_short_label({
        "name": "Kitsun Noct", "level_min": 65, "level_max": 65,
    }) == "KITS · 65"
    assert MiniPathCanvas._alpha_pal_short_label({
        "name": "Dualith", "level_min": 55, "level_max": 75,
    }) == "DUAL · 55–75"


def test_pause_menu_exposes_default_on_alpha_pal_layer_toggle():
    app = QApplication.instance() or QApplication([])
    overlay = PathOverlay(interaction_probe=lambda: True)

    assert overlay.alpha_pals_toggle.isChecked()
    overlay._refresh_interaction_state()
    assert not overlay.zoom_panel.isHidden()
    assert not overlay.alpha_pals_toggle.isHidden()
    label_color = overlay.alpha_pals_toggle.palette().color(QPalette.ColorRole.WindowText)
    assert label_color.lightness() >= 200

    overlay.alpha_pals_toggle.setChecked(False)
    assert overlay.canvas.alpha_pals_visible is False
    overlay.deleteLater()
    app.processEvents()


def test_pause_menu_allows_canvas_drag_and_emits_persistent_position():
    app = QApplication.instance() or QApplication([])
    overlay = PathOverlay(interaction_probe=lambda: True)
    positions = []
    overlay.position_changed.connect(lambda x, y: positions.append((x, y)))
    overlay.move(100, 100)
    overlay.show()
    overlay._refresh_interaction_state()
    app.processEvents()
    start = overlay.pos()

    QTest.mousePress(overlay.canvas, Qt.MouseButton.LeftButton, pos=QPoint(40, 40))
    QTest.mouseMove(overlay.canvas, QPoint(90, 70), delay=1)
    QTest.mouseRelease(overlay.canvas, Qt.MouseButton.LeftButton, pos=QPoint(90, 70))
    app.processEvents()

    assert overlay.pos() != start
    assert positions[-1] == (overlay.x(), overlay.y())
    overlay.hide()
    overlay.deleteLater()
    app.processEvents()


def test_window_bound_overlay_moves_and_suspends_with_palworld():
    app = QApplication.instance() or QApplication([])
    states = [
        GameWindowState(WindowRect(100, 200, 1700, 1100)),
        GameWindowState(WindowRect(2100, 100, 3380, 820)),
        GameWindowState(WindowRect(2100, 100, 3380, 820), minimized=True),
        None,
    ]
    overlay = PathOverlay(
        initial_anchor=OverlayAnchor(),
        window_probe=lambda: states.pop(0),
    )

    overlay._refresh_window_binding()
    assert (overlay.x(), overlay.y()) == (1340, 220)
    assert overlay.windowOpacity() > 0.9

    overlay._refresh_window_binding()
    assert (overlay.x(), overlay.y()) == (3020, 120)

    overlay._refresh_window_binding()
    assert overlay.windowOpacity() == 0.0
    overlay._refresh_window_binding()
    assert overlay.windowOpacity() == 0.0
    overlay.deleteLater()
    app.processEvents()


def test_overlay_is_always_click_through():
    app = QApplication.instance() or QApplication([])
    overlay = PathOverlay()

    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    overlay.deleteLater()
    app.processEvents()


def test_fast_travel_toggle_updates_plan_linked_map(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "app_data_path", lambda: tmp_path / "state.sqlite3")
    app = QApplication.instance() or QApplication([])
    window = CompanionWindow()
    midpoint = next(
        item for item in window.bundle["locations"]
        if item["name"] == "Mount Obsidian - Midpoint"
    )

    assert "171 travel waypoints" in window.fast_travel_count_label.text()
    assert "22 map-reveal watchtowers" in window.fast_travel_count_label.text()
    assert midpoint["name"] not in window._map_html(())

    window._toggle_fast_travel(midpoint["id"], True)

    assert midpoint["id"] in window.store.unlocked_ids()
    assert midpoint["name"] in window.map_view.last_html

    window.store.close()
    window.deleteLater()
    app.processEvents()
