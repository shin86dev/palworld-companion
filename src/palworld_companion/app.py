from __future__ import annotations

import json
import os
import sys
import ctypes
import difflib
import html
import math
import re
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    QThread,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QColor, QFontMetricsF, QIcon, QImage, QIntValidator, QKeySequence, QPainter, QPalette, QPen, QPolygonF, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QCompleter, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QSlider, QSplitter, QStyle, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
)

from .bundle import load_bundle
from .diagnostics import (
    DiagnosticSubmissionError,
    build_report as build_diagnostic_report,
    codex_handoff,
    report_endpoint,
    report_preview,
    submit_report,
)
from .map_asset import (
    MapAssetError,
    find_palworld_pak,
    provision_tree_map,
    provision_world_map,
    tree_map_cache_is_ready,
    tree_map_cache_path,
    world_map_cache_is_ready,
    world_map_cache_path,
)
from .models import CheckIn
from .planner import Planner
from .store import Store
from .telemetry import PalworldLiveReader
from .window_bind import (
    GameWindowState,
    OverlayAnchor,
    WindowsPalworldWindowProbe,
    overlay_anchor,
    overlay_position,
)


class WindowsGlobalHotkey(QAbstractNativeEventFilter):
    """Registers companion toggles without observing or interacting with the game."""

    PICKER_HOTKEY_ID = 0x50414C  # "PAL"
    PICKER_FALLBACK_HOTKEY_ID = 0x504150  # "PAP"
    OVERLAY_HOTKEY_ID = 0x50414D  # "PAM"
    WM_HOTKEY = 0x0312
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_NOREPEAT = 0x4000
    VK_P = 0x50
    VK_M = 0x4D
    VK_DELETE = 0x2E
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PALWORLD_PROCESS_NAMES = {"palworld.exe", "palworld-win64-shipping.exe"}

    def __init__(self, picker_callback, overlay_callback) -> None:
        super().__init__()
        self.callbacks = {
            self.PICKER_HOTKEY_ID: picker_callback,
            self.PICKER_FALLBACK_HOTKEY_ID: picker_callback,
            self.OVERLAY_HOTKEY_ID: overlay_callback,
        }
        self.registered_ids: set[int] = set()
        if sys.platform == "win32":
            self.user32 = ctypes.windll.user32
            self.kernel32 = ctypes.windll.kernel32
            self.user32.GetForegroundWindow.restype = wintypes.HWND
            self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel32.OpenProcess.restype = wintypes.HANDLE
            self.kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            ]
            self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel32.CloseHandle.restype = wintypes.BOOL
            self._register(
                self.PICKER_FALLBACK_HOTKEY_ID,
                self.MOD_ALT | self.MOD_CONTROL | self.MOD_NOREPEAT,
                self.VK_P,
            )
            self._register(
                self.OVERLAY_HOTKEY_ID,
                self.MOD_ALT | self.MOD_CONTROL | self.MOD_NOREPEAT,
                self.VK_M,
            )
            self.refresh_contextual_hotkeys()

    def _register(self, hotkey_id: int, modifiers: int, key: int) -> None:
        if hotkey_id in self.registered_ids:
            return
        if self.user32.RegisterHotKey(None, hotkey_id, modifiers, key):
            self.registered_ids.add(hotkey_id)

    def _unregister(self, hotkey_id: int) -> None:
        if hotkey_id not in self.registered_ids:
            return
        self.user32.UnregisterHotKey(None, hotkey_id)
        self.registered_ids.discard(hotkey_id)

    def _foreground_process_name(self) -> str | None:
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None
        process_id = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return None
        handle = self.kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32_768)
            size = wintypes.DWORD(len(buffer))
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return Path(buffer.value).name.lower()
        finally:
            self.kernel32.CloseHandle(handle)

    def refresh_contextual_hotkeys(self) -> None:
        if sys.platform != "win32":
            return
        if self.picker_hotkey_enabled_for_process(self._foreground_process_name()):
            self._register(self.PICKER_HOTKEY_ID, self.MOD_NOREPEAT, self.VK_DELETE)
        else:
            self._unregister(self.PICKER_HOTKEY_ID)

    @classmethod
    def picker_hotkey_enabled_for_process(cls, process_name: str | None) -> bool:
        return process_name is not None and process_name.casefold() in cls.PALWORLD_PROCESS_NAMES

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            native_message = wintypes.MSG.from_address(int(message))
            hotkey_id = int(native_message.wParam)
            if native_message.message == self.WM_HOTKEY and hotkey_id in self.callbacks:
                QTimer.singleShot(0, self.callbacks[hotkey_id])
                return True, 0
        return False, 0

    def close(self) -> None:
        if sys.platform != "win32":
            return
        for hotkey_id in tuple(self.registered_ids):
            self.user32.UnregisterHotKey(None, hotkey_id)
        self.registered_ids.clear()


class LocalMapView(QTextBrowser):
    """Network-free secondary plan view without the heavyweight WebEngine runtime."""

    def __init__(self) -> None:
        super().__init__()
        self.last_html = ""

    def set_map_html(self, content: str) -> None:
        self.last_html = content
        self.setHtml(content)


class MiniPathCanvas(QWidget):
    """Live local-map crop with a heading and destination indicator."""

    zoom_changed = Signal(int)

    MAP_SIZE = 8192.0
    IMAGE_SCALE = MAP_SIZE / 2000.0
    TRANSL_X = 375247.0
    TRANSL_Y = -18.0
    GAME_SCALE = 725.0
    WORLD_MIN = -1000.0
    WORLD_MAX = 1000.0
    LEGACY_TRANSL_X = 123888.0
    LEGACY_TRANSL_Y = 158000.0
    LEGACY_SCALE = 459.0
    WORLD_TREE_TRANSL_X = 358540.0
    WORLD_TREE_TRANSL_Y = -382365.0
    WORLD_TREE_SCALE = 724.0
    WORLD_TREE_COORD_RANGE = 2500.0
    WORLD_TREE_PIXEL_OFFSET_X = 1760.0
    WORLD_TREE_PIXEL_OFFSET_Y = 2571.0
    MIN_ZOOM = 0
    MAX_ZOOM = 100
    DEFAULT_ZOOM = 40
    MIN_CROP_WIDTH = 260.0
    MAX_CROP_WIDTH = 3200.0

    def __init__(self) -> None:
        super().__init__()
        self.target: dict | None = None
        self.live_sample: dict | None = None
        self.landmarks: tuple[dict, ...] = ()
        self.unlocked_landmark_ids: set[str] = set()
        self.live_unlocked_waypoint_keys: set[str] | None = None
        self.live_cleared_alpha_keys: set[str] | None = None
        self.alpha_pals_visible = True
        self.map_image = QImage()
        self.tree_map_image = QImage()
        self.map_source: str | None = None
        self.tree_map_source: str | None = None
        self.active_region = "palpagos"
        self.zoom = self.DEFAULT_ZOOM
        self.crop_width = self._crop_width_for_zoom(self.zoom)
        self.setMinimumSize(300, 150)

    @classmethod
    def _crop_width_for_zoom(cls, zoom: int) -> float:
        normalized = (max(cls.MIN_ZOOM, min(cls.MAX_ZOOM, int(zoom))) - cls.MIN_ZOOM) / (
            cls.MAX_ZOOM - cls.MIN_ZOOM
        )
        return cls.MAX_CROP_WIDTH * ((cls.MIN_CROP_WIDTH / cls.MAX_CROP_WIDTH) ** normalized)

    def set_zoom(self, zoom: int) -> None:
        zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, int(zoom)))
        if zoom == self.zoom:
            return
        self.zoom = zoom
        self.crop_width = self._crop_width_for_zoom(zoom)
        self.zoom_changed.emit(zoom)
        self.update()

    def adjust_zoom(self, steps: int) -> None:
        self.set_zoom(self.zoom + (int(steps) * 5))

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta:
            self.adjust_zoom(round(delta / 120))
            event.accept()
            return
        super().wheelEvent(event)

    def set_destination(self, target: dict | None) -> None:
        self.target = target
        self.update()

    def set_map_image(self, path: Path | None) -> bool:
        self.map_source = str(path) if path is not None else None
        self.map_image = QImage(str(path)) if path is not None else QImage()
        self.update()
        return not self.map_image.isNull()

    def set_tree_map_image(self, path: Path | None) -> bool:
        self.tree_map_source = str(path) if path is not None else None
        self.tree_map_image = QImage(str(path)) if path is not None else QImage()
        self.update()
        return not self.tree_map_image.isNull()

    def set_live_sample(self, sample: dict | None) -> None:
        self.live_sample = sample
        self.update()

    def set_landmarks(self, landmarks: tuple[dict, ...], unlocked_ids: set[str] | None = None) -> None:
        self.landmarks = landmarks
        self.unlocked_landmark_ids = set(unlocked_ids or ())
        self.update()

    def set_live_unlocked_waypoint_keys(self, unlocked_keys: set[str] | None) -> None:
        self.live_unlocked_waypoint_keys = None if unlocked_keys is None else {
            str(key).upper() for key in unlocked_keys
        }
        self.update()

    def set_live_cleared_alpha_keys(self, cleared_keys: set[str] | None) -> None:
        self.live_cleared_alpha_keys = None if cleared_keys is None else {
            str(key).upper() for key in cleared_keys
        }
        self.update()

    def set_alpha_pals_visible(self, visible: bool) -> None:
        self.alpha_pals_visible = bool(visible)
        self.update()

    @classmethod
    def unreal_to_base_image(cls, x: float, y: float) -> QPointF:
        world_x = (y - cls.TRANSL_Y) / cls.GAME_SCALE
        world_y = (x + cls.TRANSL_X) / cls.GAME_SCALE
        pixel_x = (world_x - cls.WORLD_MIN) * cls.IMAGE_SCALE
        pixel_y = (cls.WORLD_MAX - world_y) * cls.IMAGE_SCALE
        return QPointF(pixel_x, pixel_y)

    @classmethod
    def world_tree_map_coordinates(cls, x: float, y: float) -> QPointF:
        return QPointF(
            (y - cls.WORLD_TREE_TRANSL_Y) / cls.WORLD_TREE_SCALE,
            (x + cls.WORLD_TREE_TRANSL_X) / cls.WORLD_TREE_SCALE,
        )

    @classmethod
    def map_coordinates_to_unreal(cls, region: str, x: float, y: float) -> QPointF:
        if region == "world-tree":
            return QPointF(
                (y * cls.WORLD_TREE_SCALE) - cls.WORLD_TREE_TRANSL_X,
                (x * cls.WORLD_TREE_SCALE) + cls.WORLD_TREE_TRANSL_Y,
            )
        if region == "palpagos":
            return cls.legacy_to_unreal(x, y)
        raise ValueError(f"Unsupported coordinate region: {region}")

    @classmethod
    def world_tree_unreal_to_base_image(cls, x: float, y: float) -> QPointF:
        point = cls.world_tree_map_coordinates(x, y)
        span = cls.WORLD_TREE_COORD_RANGE * 2
        pixel_x = (
            (point.x() + cls.WORLD_TREE_COORD_RANGE) * cls.MAP_SIZE / span
            + cls.WORLD_TREE_PIXEL_OFFSET_X
        )
        pixel_y = (
            (cls.WORLD_TREE_COORD_RANGE - point.y()) * cls.MAP_SIZE / span
            + cls.WORLD_TREE_PIXEL_OFFSET_Y
        )
        return QPointF(
            max(0.0, min(cls.MAP_SIZE - 1, pixel_x)),
            max(0.0, min(cls.MAP_SIZE - 1, pixel_y)),
        )

    @classmethod
    def region_for_unreal_position(cls, x: float, y: float) -> str:
        palpagos_x = (y - cls.TRANSL_Y) / cls.GAME_SCALE
        palpagos_y = (x + cls.TRANSL_X) / cls.GAME_SCALE
        if abs(palpagos_x) <= cls.WORLD_MAX and abs(palpagos_y) <= cls.WORLD_MAX:
            return "palpagos"
        tree = cls.world_tree_map_coordinates(x, y)
        if abs(tree.x()) <= cls.WORLD_TREE_COORD_RANGE and abs(tree.y()) <= cls.WORLD_TREE_COORD_RANGE:
            return "world-tree"
        return "unknown"

    @classmethod
    def region_map_coordinates(cls, region: str, x: float, y: float) -> QPointF:
        if region == "world-tree":
            return cls.world_tree_map_coordinates(x, y)
        return QPointF(
            (y - cls.TRANSL_Y) / cls.GAME_SCALE,
            (x + cls.TRANSL_X) / cls.GAME_SCALE,
        )

    @classmethod
    def legacy_to_unreal(cls, x: float, y: float) -> QPointF:
        unreal_x = (y * cls.LEGACY_SCALE) - cls.LEGACY_TRANSL_X
        unreal_y = (x * cls.LEGACY_SCALE) + cls.LEGACY_TRANSL_Y
        return QPointF(unreal_x, unreal_y)

    @classmethod
    def unreal_to_legacy(cls, x: float, y: float) -> QPointF:
        legacy_x = (y - cls.LEGACY_TRANSL_Y) / cls.LEGACY_SCALE
        legacy_y = (x + cls.LEGACY_TRANSL_X) / cls.LEGACY_SCALE
        return QPointF(legacy_x, legacy_y)

    def _actual_image_point(self, point: QPointF, image: QImage | None = None) -> QPointF:
        if image is None:
            image = self.map_image
        if image.isNull():
            return point
        return QPointF(
            point.x() * image.width() / self.MAP_SIZE,
            point.y() * image.height() / self.MAP_SIZE,
        )

    def _draw_player_arrow(self, painter: QPainter, center: QPointF, heading: float) -> None:
        radians = math.radians(heading)
        forward = QPointF(math.sin(radians), -math.cos(radians))
        side = QPointF(-forward.y(), forward.x())
        tip = center + forward * 15
        back = center - forward * 9
        arrow = QPolygonF([tip, back + side * 8, center - forward * 3, back - side * 8])
        painter.setPen(QPen(QColor("#071018"), 4))
        painter.setBrush(QColor("#ffd21f"))
        painter.drawPolygon(arrow)
        painter.setPen(QPen(QColor("#f6fbff"), 1.25))
        painter.drawPolygon(arrow)

    def _draw_waypoint_glyph(
        self,
        painter: QPainter,
        point: QPointF,
        *,
        unlocked: bool | None,
        selected: bool,
    ) -> None:
        if selected:
            signal = QColor("#ffb454")
        elif unlocked:
            signal = QColor("#52c9ff")
        else:
            signal = QColor("#71808f")
        outline = QColor("#071018")
        head = point + QPointF(0, -3)
        pointer = QPolygonF([
            point + QPointF(-5, 2),
            point + QPointF(5, 2),
            point + QPointF(0, 11),
        ])
        if selected:
            painter.setPen(QPen(QColor("#ffb454"), 2))
            painter.setBrush(QColor(255, 180, 84, 45))
            painter.drawEllipse(point, 14, 14)
        painter.setPen(QPen(outline, 4))
        painter.setBrush(signal)
        painter.drawPolygon(pointer)
        painter.drawEllipse(head, 8, 8)
        painter.setPen(QPen(QColor("#edf5ff"), 1.5))
        painter.drawPolygon(pointer)
        painter.drawEllipse(head, 8, 8)
        if unlocked is None:
            painter.setPen(QPen(outline, 1.5))
            painter.setBrush(QColor(7, 16, 24, 210))
            painter.drawEllipse(head, 2.5, 2.5)
        else:
            self._draw_waypoint_state_mark(painter, head, unlocked=unlocked)

    def _draw_watchtower_glyph(
        self,
        painter: QPainter,
        point: QPointF,
        *,
        unlocked: bool | None,
        selected: bool,
    ) -> None:
        if selected:
            signal = QColor("#ffb454")
        elif unlocked:
            signal = QColor("#6edcff")
        else:
            signal = QColor("#71808f")
        outline = QColor("#071018")
        diamond = QPolygonF([
            point + QPointF(0, -12),
            point + QPointF(12, 0),
            point + QPointF(0, 12),
            point + QPointF(-12, 0),
        ])
        inner = QPolygonF([
            point + QPointF(0, -6),
            point + QPointF(6, 0),
            point + QPointF(0, 6),
            point + QPointF(-6, 0),
        ])
        painter.setPen(QPen(outline, 4))
        painter.setBrush(signal)
        painter.drawPolygon(diamond)
        painter.setPen(QPen(QColor("#edf5ff"), 1.5))
        painter.drawPolygon(diamond)
        if unlocked is None:
            painter.setPen(QPen(outline, 1.5))
            painter.setBrush(QColor(7, 16, 24, 210))
            painter.drawPolygon(inner)
        else:
            self._draw_waypoint_state_mark(painter, point, unlocked=unlocked)

    def _draw_waypoint_state_mark(self, painter: QPainter, point: QPointF, *, unlocked: bool) -> None:
        """Draw a check/X state pair inside the waypoint instead of a satellite badge."""
        painter.save()
        mark_pen = QPen(QColor("#071018") if unlocked else QColor("#edf5ff"), 2.6)
        mark_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        mark_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(mark_pen)
        if unlocked:
            painter.drawLine(point + QPointF(-4.5, 0), point + QPointF(-1.2, 3.5))
            painter.drawLine(point + QPointF(-1.2, 3.5), point + QPointF(5.2, -4))
        else:
            painter.drawLine(point + QPointF(-4, -4), point + QPointF(4, 4))
            painter.drawLine(point + QPointF(4, -4), point + QPointF(-4, 4))
        painter.restore()

    def _draw_alpha_pal_glyph(
        self,
        painter: QPainter,
        point: QPointF,
        *,
        selected: bool,
        cleared: bool | None = None,
    ) -> None:
        if selected:
            accent = QColor("#ffb454")
        elif cleared is True:
            accent = QColor("#5de0ad")
        elif cleared is False:
            accent = QColor("#ff6f91")
        else:
            accent = QColor("#b58ca5")
        outline = QColor("#071018")
        if selected:
            painter.setPen(QPen(accent, 2))
            painter.setBrush(QColor(255, 180, 84, 40))
            painter.drawEllipse(point, 15, 15)
        star = QPolygonF()
        for index in range(10):
            angle = math.radians(-90 + (index * 36))
            radius = 11 if index % 2 == 0 else 4.5
            star.append(point + QPointF(math.cos(angle) * radius, math.sin(angle) * radius))
        painter.setPen(QPen(outline, 4))
        painter.setBrush(accent)
        painter.drawPolygon(star)
        painter.setPen(QPen(QColor("#fff7f9"), 1.5))
        painter.drawPolygon(star)
        if cleared is True:
            badge = point + QPointF(8, 8)
            painter.setPen(QPen(QColor("#071018"), 3))
            painter.setBrush(QColor("#5de0ad"))
            painter.drawEllipse(badge, 6, 6)
            check_pen = QPen(QColor("#071018"), 2.25)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(check_pen)
            painter.drawLine(badge + QPointF(-3, 0), badge + QPointF(-1, 3))
            painter.drawLine(badge + QPointF(-1, 3), badge + QPointF(4, -3))

    @staticmethod
    def _alpha_pal_short_label(landmark: dict) -> str:
        first_word = str(landmark.get("name", "Pal")).split()[0]
        prefix = "".join(character for character in first_word if character.isalnum()).upper()[:4] or "PAL"
        level_min = landmark.get("level_min", "?")
        level_max = landmark.get("level_max", level_min)
        level = str(level_min) if level_min == level_max else f"{level_min}–{level_max}"
        return f"{prefix} · {level}"

    def _draw_alpha_pal_label(
        self,
        painter: QPainter,
        point: QPointF,
        landmark: dict,
        *,
        selected: bool,
        cleared: bool | None = None,
    ) -> None:
        label = self._alpha_pal_short_label(landmark)
        if cleared is True:
            label = f"{label}  ✓"
        elif cleared is False:
            label = f"{label}  1ST"
        painter.save()
        font = painter.font()
        font.setFamily("Segoe UI")
        font.setPixelSize(8)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        text_width = metrics.horizontalAdvance(label)
        box_width = text_width + 7
        box_height = max(11.0, metrics.height() + 2)
        box = QRectF(
            point.x() - (box_width / 2),
            point.y() - 15 - box_height,
            box_width,
            box_height,
        )
        if selected:
            accent = QColor("#ffb454")
        elif cleared is True:
            accent = QColor("#5de0ad")
        elif cleared is False:
            accent = QColor("#ff6f91")
        else:
            accent = QColor("#b58ca5")
        painter.setPen(QPen(accent, 1))
        painter.setBrush(QColor(7, 16, 24, 220))
        painter.drawRoundedRect(box, 2, 2)
        painter.setPen(QColor("#fff7f9"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def _landmark_image_point(self, landmark: dict, region: str) -> QPointF | None:
        landmark_region = landmark.get("region", "palpagos")
        if landmark_region != region:
            return None
        if landmark.get("world_x") is not None and landmark.get("world_y") is not None:
            unreal = QPointF(float(landmark["world_x"]), float(landmark["world_y"]))
        elif landmark.get("x") is not None and landmark.get("y") is not None:
            unreal = self.legacy_to_unreal(float(landmark["x"]), float(landmark["y"]))
        else:
            return None
        if region == "world-tree":
            point = self.world_tree_unreal_to_base_image(unreal.x(), unreal.y())
            return self._actual_image_point(point, self.tree_map_image)
        point = self.unreal_to_base_image(unreal.x(), unreal.y())
        return self._actual_image_point(point, self.map_image)

    def _draw_landmarks(
        self,
        painter: QPainter,
        player_image: QPointF,
        center: QPointF,
        crop_width: float,
        crop_height: float,
        region: str,
    ) -> None:
        visible: list[tuple[float, QPointF, dict]] = []
        for landmark in self.landmarks:
            image_point = self._landmark_image_point(landmark, region)
            if image_point is None:
                continue
            dx = (image_point.x() - player_image.x()) * self.width() / crop_width
            dy = (image_point.y() - player_image.y()) * self.height() / crop_height
            screen_point = center + QPointF(dx, dy)
            if 10 <= screen_point.x() <= self.width() - 10 and 10 <= screen_point.y() <= self.height() - 10:
                visible.append((dx * dx + dy * dy, screen_point, landmark))

        target_id = self.target.get("id") if self.target else None
        travel = [item for item in visible if item[2].get("kind") != "alpha_pal"]
        alpha_pals = [item for item in visible if item[2].get("kind") == "alpha_pal"]
        travel.sort(key=lambda item: (item[2].get("id") != target_id, item[0]))
        alpha_pals.sort(key=lambda item: (item[2].get("id") != target_id, item[0]))
        zoomed_out_fraction = max(
            0.0,
            min(
                1.0,
                (crop_width - self.MIN_CROP_WIDTH) / (self.MAX_CROP_WIDTH - self.MIN_CROP_WIDTH),
            ),
        )
        minimum_travel_spacing = 16.0 + (10.0 * zoomed_out_fraction)
        spaced_travel: list[tuple[float, QPointF, dict]] = []
        for candidate in travel:
            point = candidate[1]
            if any(
                math.hypot(point.x() - kept[1].x(), point.y() - kept[1].y())
                < minimum_travel_spacing
                for kept in spaced_travel
            ):
                continue
            spaced_travel.append(candidate)
            if len(spaced_travel) == 8:
                break

        for _distance, point, landmark in spaced_travel:
            landmark_id = landmark.get("id")
            upstream_key = str(landmark.get("upstream_key", "")).upper()
            live_state_known = self.live_unlocked_waypoint_keys is not None and bool(upstream_key)
            unlocked: bool | None = (
                upstream_key in self.live_unlocked_waypoint_keys
                if live_state_known
                else True if landmark_id in self.unlocked_landmark_ids else None
            )
            draw_glyph = (
                self._draw_watchtower_glyph
                if landmark.get("waypoint_class") == "watchtower"
                else self._draw_waypoint_glyph
            )
            draw_glyph(
                painter,
                point,
                unlocked=unlocked,
                selected=landmark_id == target_id,
            )
        if self.alpha_pals_visible:
            for _distance, point, landmark in alpha_pals:
                first_clear_key = str(landmark.get("first_clear_key", "")).upper()
                clear_state_known = self.live_cleared_alpha_keys is not None and bool(first_clear_key)
                cleared = (
                    first_clear_key in self.live_cleared_alpha_keys
                    if clear_state_known
                    else None
                )
                self._draw_alpha_pal_glyph(
                    painter,
                    point,
                    selected=landmark.get("id") == target_id,
                    cleared=cleared,
                )
            for _distance, point, landmark in alpha_pals:
                first_clear_key = str(landmark.get("first_clear_key", "")).upper()
                clear_state_known = self.live_cleared_alpha_keys is not None and bool(first_clear_key)
                cleared = (
                    first_clear_key in self.live_cleared_alpha_keys
                    if clear_state_known
                    else None
                )
                self._draw_alpha_pal_label(
                    painter,
                    point,
                    landmark,
                    selected=landmark.get("id") == target_id,
                    cleared=cleared,
                )

    def _draw_live_path(self, painter: QPainter, player_image: QPointF, center: QPointF, crop_width: float, crop_height: float, region: str) -> None:
        if not self.target:
            return
        target_image = self._landmark_image_point(self.target, region)
        if target_image is None:
            return
        dx = (target_image.x() - player_image.x()) * self.width() / crop_width
        dy = (target_image.y() - player_image.y()) * self.height() / crop_height
        margin = 15.0
        half_width = max(self.width() / 2 - margin, 1.0)
        half_height = max(self.height() / 2 - margin, 1.0)
        scale = min(1.0, half_width / max(abs(dx), 1.0), half_height / max(abs(dy), 1.0))
        endpoint = center + QPointF(dx * scale, dy * scale)
        painter.setPen(QPen(QColor("#ffb454"), 3, Qt.PenStyle.DashLine))
        painter.drawLine(center, endpoint)
        if scale < 1.0:
            length = max(math.hypot(dx, dy), 1.0)
            forward = QPointF(dx / length, dy / length)
            side = QPointF(-forward.y(), forward.x())
            base = endpoint - forward * 14
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffb454"))
            painter.drawPolygon(QPolygonF([endpoint, base + side * 7, base - side * 7]))
        else:
            painter.setPen(QPen(QColor("#1b1006"), 2))
            painter.setBrush(QColor("#ffb454"))
            painter.drawEllipse(endpoint, 7, 7)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#10151d"))
        if self.live_sample is not None:
            position = self.live_sample["position"]
            world_x = float(position["x"])
            world_y = float(position["y"])
            region = self.region_for_unreal_position(world_x, world_y)
            self.active_region = region
            if region == "world-tree":
                active_image = self.tree_map_image
                base_point = self.world_tree_unreal_to_base_image(world_x, world_y)
            elif region == "palpagos":
                active_image = self.map_image
                base_point = self.unreal_to_base_image(world_x, world_y)
            else:
                active_image = QImage()
                base_point = QPointF(self.MAP_SIZE / 2, self.MAP_SIZE / 2)
            player_image = self._actual_image_point(base_point, active_image)
            crop_width = self.crop_width * (active_image.width() / self.MAP_SIZE if not active_image.isNull() else 1.0)
            crop_height = crop_width * self.height() / max(self.width(), 1)
            if not active_image.isNull():
                source = QRectF(
                    player_image.x() - crop_width / 2,
                    player_image.y() - crop_height / 2,
                    crop_width,
                    crop_height,
                )
                painter.drawImage(QRectF(self.rect()), active_image, source)
            else:
                painter.setPen(QPen(QColor("#27364a"), 1))
                for x in range(0, self.width(), 40):
                    painter.drawLine(x, 0, x, self.height())
                for y in range(0, self.height(), 40):
                    painter.drawLine(0, y, self.width(), y)
            center = QPointF(self.width() / 2, self.height() / 2)
            self._draw_landmarks(painter, player_image, center, crop_width, crop_height, region)
            self._draw_live_path(painter, player_image, center, crop_width, crop_height, region)
            self._draw_player_arrow(painter, center, float(self.live_sample["heading_degrees"]))
            painter.setPen(QColor("white"))
            region_label = "WORLD TREE · N" if region == "world-tree" else "N"
            if region == "unknown":
                region_label = "UNSUPPORTED REGION"
            painter.drawText(0, 4, self.width(), 18, Qt.AlignmentFlag.AlignHCenter, region_label)
            return

        painter.setPen(QPen(QColor("#27364a"), 1))
        for x in range(0, self.width(), 40):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 40):
            painter.drawLine(0, y, self.width(), y)
        painter.setPen(QColor("#8fa4bd"))
        painter.drawText(0, 4, self.width(), 20, Qt.AlignmentFlag.AlignHCenter, "N")
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for Palworld")


def local_map_image_path() -> Path | None:
    """Resolve an override, stable private cache, or development-only preview."""
    if override := os.environ.get("PALPLUS_MAP_IMAGE"):
        path = Path(override)
        return path if path.is_file() else None
    cache_path = world_map_cache_path()
    if cache_path.is_file():
        return cache_path
    if os.environ.get("PALPLUS_DISABLE_AUDIT_MAP") == "1":
        return None
    audit_asset = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Temp"
        / "palworld-map-source-audit"
        / "pst"
        / "resources"
        / "assets"
        / "maps"
        / "T_WorldMap.webp"
    )
    return audit_asset if audit_asset.is_file() else None


def local_tree_map_image_path() -> Path | None:
    """Resolve a private World Tree override, stable cache, or development preview."""
    if override := os.environ.get("PALPLUS_TREE_MAP_IMAGE"):
        path = Path(override)
        return path if path.is_file() else None
    cache_path = tree_map_cache_path()
    if cache_path.is_file():
        return cache_path
    if os.environ.get("PALPLUS_DISABLE_AUDIT_MAP") == "1":
        return None
    audit_asset = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Temp"
        / "palworld-map-source-audit"
        / "pst"
        / "resources"
        / "assets"
        / "maps"
        / "T_TreeMap.webp"
    )
    return audit_asset if audit_asset.is_file() else None


def automatic_map_provision_needed() -> bool:
    world_needed = not os.environ.get("PALPLUS_MAP_IMAGE") and not world_map_cache_is_ready()
    tree_needed = not os.environ.get("PALPLUS_TREE_MAP_IMAGE") and not tree_map_cache_is_ready()
    return world_needed or tree_needed


def provision_local_world_map() -> Path:
    pak_path = find_palworld_pak()
    if pak_path is None:
        raise MapAssetError("Palworld's installed Steam archive was not found.")
    return provision_world_map(pak_path)


def provision_local_tree_map() -> Path:
    pak_path = find_palworld_pak()
    if pak_path is None:
        raise MapAssetError("Palworld's installed Steam archive was not found.")
    return provision_tree_map(pak_path)


def provision_local_maps() -> dict[str, str]:
    paths: dict[str, str] = {}
    world_path = local_map_image_path()
    if not os.environ.get("PALPLUS_MAP_IMAGE") and not world_map_cache_is_ready():
        world_path = provision_local_world_map()
    if world_path is not None:
        paths["palpagos"] = str(world_path)
    tree_path = local_tree_map_image_path()
    if not os.environ.get("PALPLUS_TREE_MAP_IMAGE") and not tree_map_cache_is_ready():
        tree_path = provision_local_tree_map()
    if tree_path is not None:
        paths["world-tree"] = str(tree_path)
    return paths


class MapProvisionWorker(QObject):
    ready = Signal(object)
    failed = Signal(str)
    finished = Signal()

    @Slot()
    def run(self) -> None:
        try:
            self.ready.emit(provision_local_maps())
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()


class DiagnosticReportWorker(QObject):
    """Send one user-approved report without blocking the companion window."""

    sent = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, report: dict) -> None:
        super().__init__()
        self.report = report

    @Slot()
    def run(self) -> None:
        try:
            self.sent.emit(submit_report(self.report))
        except DiagnosticSubmissionError as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()


class WindowsGameUiProbe:
    """Reports when Palworld is foreground and has exposed the system cursor."""

    CURSOR_SHOWING = 0x00000001
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PALWORLD_PROCESS_NAMES = WindowsGlobalHotkey.PALWORLD_PROCESS_NAMES

    class CursorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hCursor", wintypes.HANDLE),
            ("ptScreenPos", wintypes.POINT),
        ]

    def __init__(self) -> None:
        self.available = sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") != "offscreen"
        if not self.available:
            return
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.GetCursorInfo.argtypes = [ctypes.POINTER(self.CursorInfo)]
        self.user32.GetCursorInfo.restype = wintypes.BOOL
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    @classmethod
    def permits_interaction(cls, process_name: str | None, cursor_showing: bool) -> bool:
        return bool(
            cursor_showing
            and process_name is not None
            and process_name.casefold() in cls.PALWORLD_PROCESS_NAMES
        )

    def _foreground_process_name(self) -> str | None:
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None
        process_id = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return None
        handle = self.kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32_768)
            size = wintypes.DWORD(len(buffer))
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return Path(buffer.value).name.lower()
        finally:
            self.kernel32.CloseHandle(handle)

    def __call__(self) -> bool:
        if not self.available:
            return False
        cursor = self.CursorInfo()
        cursor.cbSize = ctypes.sizeof(self.CursorInfo)
        if not self.user32.GetCursorInfo(ctypes.byref(cursor)):
            return False
        return self.permits_interaction(
            self._foreground_process_name(),
            bool(cursor.flags & self.CURSOR_SHOWING),
        )


class MinimalOverlayFrame(QWidget):
    """A+C reaction-pass frame: obvious corners with a restrained precision keyline."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(1.5, 1.5, max(0, self.width() - 3), max(0, self.height() - 3))
        keyline = QColor(61, 139, 255, 150)
        signal = QColor(61, 139, 255, 235)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(keyline, 1))
        painter.drawRect(bounds)

        corner = 18.0
        painter.setPen(QPen(signal, 2))
        left, top, right, bottom = bounds.left(), bounds.top(), bounds.right(), bounds.bottom()
        for start, end in (
            (QPointF(left, top + corner), QPointF(left, top)),
            (QPointF(left, top), QPointF(left + corner, top)),
            (QPointF(right - corner, top), QPointF(right, top)),
            (QPointF(right, top), QPointF(right, top + corner)),
            (QPointF(left, bottom - corner), QPointF(left, bottom)),
            (QPointF(left, bottom), QPointF(left + corner, bottom)),
            (QPointF(right - corner, bottom), QPointF(right, bottom)),
            (QPointF(right, bottom), QPointF(right, bottom - corner)),
        ):
            painter.drawLine(start, end)

        center = bounds.center().x()
        painter.setPen(QPen(signal, 1.5))
        painter.drawLine(QPointF(center - 9, top), QPointF(center - 3, top))
        painter.drawLine(QPointF(center + 3, top), QPointF(center + 9, top))
        painter.drawLine(QPointF(center, top), QPointF(center, top + 6))


class PathOverlay(QWidget):
    """Live minimap that becomes interactive only when Palworld exposes its cursor."""

    zoom_changed = Signal(int)
    alpha_pals_visibility_changed = Signal(bool)
    position_changed = Signal(int, int)
    anchor_changed = Signal(float, float)

    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_NOACTIVATE = 0x08000000
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    SWP_FRAMECHANGED = 0x0020

    def __init__(
        self,
        *,
        initial_zoom: int = MiniPathCanvas.DEFAULT_ZOOM,
        initial_alpha_pals_visible: bool = True,
        interaction_probe: Callable[[], bool] | None = None,
        initial_anchor: OverlayAnchor | None = None,
        window_probe: Callable[[], GameWindowState | None] | None = None,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setWindowTitle("PalPlus Path")
        self.setFixedSize(340, 240)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWindowOpacity(0.94)
        self._set_overlay_style()
        self.placed = False
        self.interaction_probe = interaction_probe or WindowsGameUiProbe()
        self.window_probe = window_probe or WindowsPalworldWindowProbe()
        self.window_binding_enabled = bool(
            window_probe is not None or getattr(self.window_probe, "available", False)
        )
        self.overlay_anchor = (initial_anchor or OverlayAnchor()).clamped()
        self.bound_window_state: GameWindowState | None = None
        self.interaction_enabled = False
        self._drag_start_global: QPoint | None = None
        self._drag_start_window: QPoint | None = None
        self.live_reader: PalworldLiveReader | None = None
        self.live_error: str | None = None
        self.live_status = "Connecting live read-only minimap"
        self.next_live_connect_at = 0.0
        self.canvas = MiniPathCanvas()
        self.canvas.installEventFilter(self)
        self.canvas.set_zoom(initial_zoom)
        self.canvas.set_alpha_pals_visible(initial_alpha_pals_visible)
        self.canvas.zoom_changed.connect(self._zoom_changed)
        initial_map = local_map_image_path()
        self.canvas.set_map_image(initial_map)
        self.canvas.set_tree_map_image(local_tree_map_image_path())
        self.map_provision_state = "idle" if automatic_map_provision_needed() else "ready"
        self.map_provision_error: str | None = None
        self._map_thread: QThread | None = None
        self._map_worker: MapProvisionWorker | None = None
        self.hud = QWidget()
        self.hud.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        hud_layout = QVBoxLayout(self.hud)
        hud_layout.setContentsMargins(8, 8, 8, 7)
        hud_layout.setSpacing(3)
        self.destination_label = QLabel()
        self.destination_label.setStyleSheet(
            "background: rgba(6, 15, 22, 205); border: 1px solid #3b566d; "
            "padding: 3px 6px; font-size: 15px; font-weight: 600; color: white"
        )
        self.destination_label.hide()
        self.direction_label = QLabel()
        self.direction_label.setStyleSheet(
            "background: rgba(6, 15, 22, 205); padding: 2px 6px; "
            "font-size: 13px; color: #ffb454"
        )
        self.direction_label.hide()
        self.disclaimer_label = QLabel("Connecting live read-only minimap...")
        self.disclaimer_label.setStyleSheet(
            "background: rgba(6, 15, 22, 190); padding: 2px 5px; color: #9aabbd; font-size: 10px"
        )
        hud_layout.addWidget(self.destination_label, 0, Qt.AlignmentFlag.AlignLeft)
        hud_layout.addWidget(self.direction_label, 0, Qt.AlignmentFlag.AlignLeft)
        hud_layout.addStretch(1)
        hud_layout.addWidget(self.disclaimer_label, 0, Qt.AlignmentFlag.AlignLeft)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.canvas, 0, 0)
        layout.addWidget(self.hud, 0, 0)
        self.frame = MinimalOverlayFrame()
        layout.addWidget(self.frame, 0, 0)
        self.zoom_panel = QWidget()
        self.zoom_panel.setObjectName("zoomPanel")
        self.zoom_panel.setStyleSheet(
            "QWidget#zoomPanel { background: rgba(6, 15, 22, 225); border: 1px solid #3b566d; "
            "border-radius: 3px; }"
            "QLabel { color: #edf5ff; border: none; background: transparent; font-size: 10px; }"
            "QSlider { background: transparent; border: none; }"
            "QSlider::groove:horizontal { height: 3px; background: #33485c; }"
            "QSlider::sub-page:horizontal { background: #3d8bff; }"
            "QSlider::handle:horizontal { width: 10px; margin: -4px 0; border-radius: 5px; "
            "background: #edf5ff; border: 1px solid #3d8bff; }"
        )
        zoom_layout = QHBoxLayout(self.zoom_panel)
        zoom_layout.setContentsMargins(6, 3, 6, 3)
        zoom_layout.setSpacing(5)
        zoom_layout.addWidget(QLabel("−"))
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(MiniPathCanvas.MIN_ZOOM, MiniPathCanvas.MAX_ZOOM)
        self.zoom_slider.setValue(self.canvas.zoom)
        self.zoom_slider.setFixedWidth(106)
        self.zoom_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.zoom_slider.setToolTip("Minimap zoom")
        self.zoom_slider.valueChanged.connect(self.canvas.set_zoom)
        zoom_layout.addWidget(self.zoom_slider)
        zoom_layout.addWidget(QLabel("+"))
        self.alpha_pals_toggle = QCheckBox("Alpha Pals")
        self.alpha_pals_toggle.setChecked(initial_alpha_pals_visible)
        self.alpha_pals_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        alpha_toggle_palette = self.alpha_pals_toggle.palette()
        alpha_toggle_palette.setColor(QPalette.ColorRole.WindowText, QColor("#edf5ff"))
        alpha_toggle_palette.setColor(QPalette.ColorRole.ButtonText, QColor("#edf5ff"))
        self.alpha_pals_toggle.setPalette(alpha_toggle_palette)
        self.alpha_pals_toggle.setStyleSheet("QCheckBox { color: #edf5ff; background: transparent; }")
        self.alpha_pals_toggle.setToolTip(
            "Show or hide Alpha Pal POIs. Live first-clear status appears when player data is available."
        )
        self.alpha_pals_toggle.toggled.connect(self._set_alpha_pals_visible)
        zoom_layout.addWidget(self.alpha_pals_toggle)
        layout.addWidget(
            self.zoom_panel,
            0,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
        )
        self.zoom_panel.hide()
        for child in (self.destination_label, self.direction_label, self.disclaimer_label, self.hud):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.setInterval(100)
        self.telemetry_timer.timeout.connect(self._poll_live_telemetry)
        self.interaction_timer = QTimer(self)
        self.interaction_timer.setInterval(150)
        self.interaction_timer.timeout.connect(self._refresh_runtime_state)

    def _set_overlay_style(self) -> None:
        self.setStyleSheet("PathOverlay { background: #0b1722; color: white; }")

    def _refresh_disclaimer(self) -> None:
        if self.map_provision_state == "preparing":
            preview = "local preview active" if not self.canvas.map_image.isNull() else "coordinate grid active"
            text = f"Preparing private map • {preview} • read-only"
        elif self.map_provision_state == "failed":
            preview = "local preview active" if not self.canvas.map_image.isNull() else "coordinate grid active"
            text = f"Map setup paused • {preview} • Del destination"
        else:
            if self.canvas.active_region == "world-tree":
                has_map = not self.canvas.tree_map_image.isNull()
                source = "World Tree local map" if has_map else "World Tree coordinate grid"
            else:
                has_map = not self.canvas.map_image.isNull()
                source = "local map" if has_map else "coordinate grid"
            action = "Del choose destination" if self.canvas.target is None else "Del change destination"
            zoom_action = "wheel / slider zoom" if self.interaction_enabled else "Tab zoom"
            text = f"{self.live_status} • {source} • {action} • {zoom_action}"
        self.disclaimer_label.setText(text)

    @Slot(int)
    def _zoom_changed(self, zoom: int) -> None:
        if self.zoom_slider.value() != zoom:
            self.zoom_slider.setValue(zoom)
        self.zoom_changed.emit(zoom)

    @Slot(bool)
    def _set_alpha_pals_visible(self, visible: bool) -> None:
        self.canvas.set_alpha_pals_visible(visible)
        self.alpha_pals_visibility_changed.emit(bool(visible))

    def _set_interaction_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.interaction_enabled:
            return
        self.interaction_enabled = enabled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not enabled)
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not enabled)
        self.zoom_panel.setVisible(enabled)
        if enabled:
            self.canvas.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self._drag_start_global = None
            self._drag_start_window = None
            self.canvas.unsetCursor()
        self._apply_native_click_through(not enabled)
        self._refresh_disclaimer()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is not self.canvas or not self.interaction_enabled:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_global = event.globalPosition().toPoint()
            self._drag_start_window = self.pos()
            self.canvas.setCursor(Qt.CursorShape.ClosedHandCursor)
            return True
        if event.type() == QEvent.Type.MouseMove and self._drag_start_global is not None:
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                return True
            candidate = self._drag_start_window + (event.globalPosition().toPoint() - self._drag_start_global)
            if self.bound_window_state is not None:
                rect = self.bound_window_state.rect
                candidate.setX(max(rect.left, min(candidate.x(), rect.right - self.width())))
                candidate.setY(max(rect.top, min(candidate.y(), rect.bottom - self.height())))
            else:
                screen = QApplication.screenAt(event.globalPosition().toPoint()) or QApplication.screenAt(self.frameGeometry().center())
                if screen is None:
                    self.move(candidate)
                    self.placed = True
                    return True
                area = screen.availableGeometry()
                candidate.setX(max(area.left(), min(candidate.x(), area.right() - self.width() + 1)))
                candidate.setY(max(area.top(), min(candidate.y(), area.bottom() - self.height() + 1)))
            self.move(candidate)
            self.placed = True
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self._drag_start_global is not None:
                if self.bound_window_state is not None:
                    self.overlay_anchor = overlay_anchor(
                        self.bound_window_state.rect,
                        (self.width(), self.height()),
                        (self.x(), self.y()),
                    )
                    self.anchor_changed.emit(
                        self.overlay_anchor.x_ratio,
                        self.overlay_anchor.y_ratio,
                    )
                else:
                    self.position_changed.emit(self.x(), self.y())
            self._drag_start_global = None
            self._drag_start_window = None
            self.canvas.setCursor(Qt.CursorShape.OpenHandCursor)
            return True
        return super().eventFilter(watched, event)

    @Slot()
    def _refresh_interaction_state(self) -> None:
        try:
            enabled = bool(self.interaction_probe())
        except Exception:
            enabled = False
        if self.window_binding_enabled:
            enabled = enabled and self.bound_window_state is not None
        self._set_interaction_enabled(enabled)

    @Slot()
    def _refresh_runtime_state(self) -> None:
        self._refresh_window_binding()
        self._refresh_interaction_state()

    def _refresh_window_binding(self) -> None:
        if not self.window_binding_enabled:
            return
        try:
            state = self.window_probe()
        except Exception:
            state = None
        self.bound_window_state = state
        if state is None or not state.displayable:
            self.setWindowOpacity(0.0)
            self._set_interaction_enabled(False)
            return
        x, y = overlay_position(
            state.rect,
            (self.width(), self.height()),
            self.overlay_anchor,
        )
        self.move(x, y)
        self.placed = True
        self.setWindowOpacity(0.94)

    def _start_map_provision(self) -> None:
        if self.map_provision_state in {"ready", "failed"}:
            self._refresh_disclaimer()
            return
        if self._map_thread is not None:
            return
        self.map_provision_state = "preparing"
        self.map_provision_error = None
        self.disclaimer_label.setToolTip("")
        self._refresh_disclaimer()
        thread = QThread(self)
        worker = MapProvisionWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.ready.connect(self._map_provision_ready)
        worker.failed.connect(self._map_provision_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._map_thread_finished)
        self._map_thread = thread
        self._map_worker = worker
        thread.start()

    @Slot(object)
    def _map_provision_ready(self, paths: dict[str, str]) -> None:
        world_path = paths.get("palpagos")
        tree_path = paths.get("world-tree")
        if world_path and not self.canvas.set_map_image(Path(world_path)):
            self._map_provision_failed(f"Generated Palpagos map cache could not be opened: {world_path}")
            return
        if tree_path and not self.canvas.set_tree_map_image(Path(tree_path)):
            self._map_provision_failed(f"Generated World Tree map cache could not be opened: {tree_path}")
            return
        self.map_provision_state = "ready"
        self.map_provision_error = None
        self.disclaimer_label.setToolTip("")
        self._refresh_disclaimer()

    @Slot(str)
    def _map_provision_failed(self, error: str) -> None:
        self.map_provision_state = "failed"
        self.map_provision_error = error
        self.disclaimer_label.setToolTip(error)
        self._refresh_disclaimer()

    @Slot()
    def _map_thread_finished(self) -> None:
        self._map_thread = None
        self._map_worker = None

    def _open_live_reader(self) -> PalworldLiveReader:
        return PalworldLiveReader(audit_observer=self._live_audit_status_changed)

    def _live_audit_status_changed(self, status: str) -> None:
        if status != "validating":
            return
        self.live_status = "Palworld update detected • validating locally…"
        self._refresh_disclaimer()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _connection_failed(self, error: Exception) -> None:
        self.live_error = str(error)
        self.canvas.set_live_unlocked_waypoint_keys(None)
        self.canvas.set_live_cleared_alpha_keys(None)
        lowered = self.live_error.lower()
        retry_seconds = 30.0 if "unsupported palworld executable" in lowered else 2.0
        self.next_live_connect_at = time.monotonic() + retry_seconds
        has_last_validated_sample = self.canvas.live_sample is not None
        if not has_last_validated_sample:
            self.canvas.set_live_sample(None)
        if self.canvas.target is not None:
            self.direction_label.setText("Waiting for live position...")
            self.direction_label.show()
        if "not running" in lowered:
            status = (
                "Live read paused • waiting for Palworld"
                if has_last_validated_sample
                else "Waiting for Palworld"
            )
        elif "local auto-audit failed" in lowered:
            status = (
                "Live read paused • Palworld update needs review"
                if has_last_validated_sample
                else "Palworld update needs review • audit report saved"
            )
        elif "unsupported palworld executable" in lowered:
            status = (
                "Live read paused • Palworld update detected"
                if has_last_validated_sample
                else "Palworld update detected • live profile needed"
            )
        else:
            status = "Live read paused • retrying"
        self.live_status = status
        self._refresh_disclaimer()

    def _start_live_reader(self) -> None:
        if self.live_reader is not None:
            return
        try:
            self.live_reader = self._open_live_reader()
            self.live_error = None
            self.next_live_connect_at = 0.0
            self._poll_live_telemetry()
        except Exception as error:
            self.live_reader = None
            self._connection_failed(error)

    def _poll_live_telemetry(self) -> None:
        if self.live_reader is None:
            if time.monotonic() >= self.next_live_connect_at:
                self._start_live_reader()
            return
        try:
            sample = self.live_reader.sample()
        except Exception as error:
            self.live_reader.close()
            self.live_reader = None
            self._connection_failed(error)
            return
        self.live_error = None
        self.live_status = "Read-only"
        unlock_state = sample.get("waypoint_unlock_state", {})
        self.canvas.set_live_unlocked_waypoint_keys(
            set(unlock_state.get("unlocked_keys", ()))
            if unlock_state.get("status") == "ready"
            else None
        )
        alpha_clear_state = sample.get("alpha_first_clear_state", {})
        self.canvas.set_live_cleared_alpha_keys(
            set(alpha_clear_state.get("cleared_keys", ()))
            if alpha_clear_state.get("status") == "ready"
            else None
        )
        self.canvas.set_live_sample(sample)
        target = self.canvas.target
        if target:
            position = sample["position"]
            current_world_x = float(position["x"])
            current_world_y = float(position["y"])
            current_region = self.canvas.region_for_unreal_position(current_world_x, current_world_y)
            target_region = target.get("region", "palpagos")
            if current_region != target_region:
                target_label = "World Tree" if target_region == "world-tree" else "Palpagos"
                self.direction_label.setText(f"{target_label} destination • cross-region route")
                self.direction_label.show()
                self._refresh_disclaimer()
                return
            current = self.canvas.region_map_coordinates(current_region, current_world_x, current_world_y)
            if target.get("world_x") is not None and target.get("world_y") is not None:
                target_map = self.canvas.region_map_coordinates(
                    target_region,
                    float(target["world_x"]),
                    float(target["world_y"]),
                )
            elif target.get("x") is not None and target.get("y") is not None:
                target_map = QPointF(float(target["x"]), float(target["y"]))
            else:
                target_map = None
            if target_map is None:
                self.direction_label.setText("Destination has no calibrated coordinate")
                self.direction_label.show()
                self._refresh_disclaimer()
                return
            dx = target_map.x() - current.x()
            dy = target_map.y() - current.y()
            distance = round(math.hypot(dx, dy))
            angle = math.degrees(math.atan2(dx, dy)) % 360
            directions = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
            direction = directions[round(angle / 45) % 8]
            self.direction_label.setText(
                f"{direction} • about {distance:,} map units • heading {sample['heading_degrees']:.0f}°"
            )
            self.direction_label.show()
        self._refresh_disclaimer()

    def _apply_native_click_through(self, click_through: bool) -> None:
        if sys.platform != "win32" or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        user32 = ctypes.windll.user32
        get_style = user32.GetWindowLongPtrW
        set_style = user32.SetWindowLongPtrW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        hwnd = wintypes.HWND(int(self.winId()))
        style = int(get_style(hwnd, self.GWL_EXSTYLE)) | self.WS_EX_NOACTIVATE
        style = style | self.WS_EX_TRANSPARENT if click_through else style & ~self.WS_EX_TRANSPARENT
        set_style(hwnd, self.GWL_EXSTYLE, style)
        user32.SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            self.SWP_NOMOVE
            | self.SWP_NOSIZE
            | self.SWP_NOZORDER
            | self.SWP_NOACTIVATE
            | self.SWP_FRAMECHANGED,
        )

    def reset_to_default_position(self) -> None:
        self.overlay_anchor = OverlayAnchor()
        if self.bound_window_state is not None:
            self.move(*overlay_position(
                self.bound_window_state.rect,
                (self.width(), self.height()),
                self.overlay_anchor,
            ))
            self.placed = True
            return
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 20, area.top() + 20)
        self.placed = True

    def set_destination(self, target: dict | None) -> None:
        self.canvas.set_destination(target)
        if target is None:
            self.destination_label.clear()
            self.destination_label.hide()
            self.direction_label.clear()
            self.direction_label.hide()
        else:
            self.destination_label.setText(target["name"])
            self.destination_label.show()
            self.direction_label.setText("Waiting for live bearing...")
            self.direction_label.show()

    def set_landmarks(self, landmarks: tuple[dict, ...], unlocked_ids: set[str] | None = None) -> None:
        self.canvas.set_landmarks(landmarks, unlocked_ids)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._set_interaction_enabled(False)
        self._apply_native_click_through(True)
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        self._start_map_provision()
        self._start_live_reader()
        self.telemetry_timer.start()
        self.interaction_timer.start()
        self._refresh_runtime_state()

    def hideEvent(self, event) -> None:
        self.telemetry_timer.stop()
        self.interaction_timer.stop()
        self._set_interaction_enabled(False)
        super().hideEvent(event)

    def cleanup(self) -> None:
        self.telemetry_timer.stop()
        self.interaction_timer.stop()
        if self.live_reader is not None:
            self.live_reader.close()
            self.live_reader = None
        thread = self._map_thread
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(30_000)

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()


def app_data_path() -> Path:
    if override := os.environ.get("PALPLUS_STATE_PATH"):
        return Path(override)
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local")) / "PalworldCompanion"
    return root / "palworld_companion.sqlite3"


def place_on_requested_monitor(app: QApplication, window: QWidget) -> None:
    """Honor an explicit test-launch monitor without changing normal placement."""
    if os.environ.get("PALPLUS_MONITOR", "").lower() != "secondary":
        return
    primary = app.primaryScreen()
    secondary = next((screen for screen in app.screens() if screen is not primary), None)
    if secondary is None:
        return
    area = secondary.availableGeometry()
    x = area.x() + max(0, (area.width() - window.width()) // 2)
    y = area.y() + max(0, (area.height() - window.height()) // 2)
    window.move(x, y)


class DestinationPicker(QWidget):
    """Keyboard-first destination chooser that does not expose the full planner."""

    MAX_VISIBLE_RESULTS = 10
    COORDINATE_LIMIT = 2500.0
    NUMBER_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"

    def __init__(
        self,
        locations: tuple[dict, ...],
        select_callback: Callable[[dict], None],
        clear_callback: Callable[[], None],
    ) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.locations = tuple(sorted(locations, key=lambda item: item["name"].casefold()))
        self.locations_by_id = {item["id"]: item for item in self.locations}
        self.live_unlocked_waypoint_keys: set[str] | None = None
        self.live_cleared_alpha_keys: set[str] | None = None
        self.coordinate_region = "palpagos"
        self.select_callback = select_callback
        self.clear_callback = clear_callback
        self.setWindowTitle("PalPlus destination")
        self.setFixedSize(420, 310)
        self.setStyleSheet(
            "QWidget { background: #101822; color: #edf5ff; }"
            "QLineEdit, QListWidget { background: #172536; border: 1px solid #39536d; "
            "border-radius: 4px; padding: 6px; }"
            "QListWidget::item { padding: 7px; }"
            "QListWidget::item:selected { background: #d79521; color: #101822; }"
            "QPushButton { background: #263a50; border: 1px solid #4d6680; "
            "border-radius: 4px; padding: 6px 10px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)
        heading = QLabel("Choose path destination")
        heading.setStyleSheet("font-weight: 600; font-size: 15px")
        layout.addWidget(heading)
        self.helper = QLabel()
        self.helper.setStyleSheet("color: #9bb0c5")
        layout.addWidget(self.helper)
        self.set_coordinate_region("palpagos")

        self.search = QLineEdit()
        self.search.setPlaceholderText("Destination")
        self.search.textChanged.connect(self._refresh_results)
        self.search.returnPressed.connect(self._choose_current)
        QShortcut(QKeySequence(Qt.Key.Key_Down), self.search, activated=lambda: self._move_selection(1))
        QShortcut(QKeySequence(Qt.Key.Key_Up), self.search, activated=lambda: self._move_selection(-1))
        layout.addWidget(self.search)

        self.results = QListWidget()
        self.results.itemActivated.connect(self._choose_item)
        layout.addWidget(self.results, 1)

        footer = QHBoxLayout()
        self.result_status = QLabel()
        self.result_status.setStyleSheet("color: #9bb0c5")
        clear_button = QPushButton("Clear destination")
        clear_button.clicked.connect(self._clear_destination)
        footer.addWidget(self.result_status, 1)
        footer.addWidget(clear_button)
        layout.addLayout(footer)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.hide)
        self._refresh_results("")

    @staticmethod
    def _display_number(value: float) -> int | float:
        return int(value) if value.is_integer() else value

    @classmethod
    def parse_coordinate_query(cls, query: str) -> tuple[int | float, int | float] | None:
        number = cls.NUMBER_PATTERN
        plain = re.fullmatch(rf"\s*\(?\s*({number})\s*(?:,|;|\s)\s*({number})\s*\)?\s*", query)
        labeled = re.fullmatch(
            rf"\s*x\s*[:=]?\s*({number})\s*(?:,|;|\s)+\s*y\s*[:=]?\s*({number})\s*",
            query,
            flags=re.IGNORECASE,
        )
        match = labeled or plain
        if match is None:
            return None
        return tuple(cls._display_number(float(value)) for value in match.groups())

    @classmethod
    def coordinate_target(cls, query: str, region: str) -> dict | None:
        parsed = cls.parse_coordinate_query(query)
        if parsed is None or region not in {"palpagos", "world-tree"}:
            return None
        x, y = parsed
        if max(abs(float(x)), abs(float(y))) > cls.COORDINATE_LIMIT:
            return None
        unreal = MiniPathCanvas.map_coordinates_to_unreal(region, float(x), float(y))
        region_label = "World Tree" if region == "world-tree" else "Palpagos"
        coordinate_system = "world-tree-map-v1" if region == "world-tree" else "palpagos-display-v1"
        return {
            "id": f"coordinate:{region}:{x}:{y}",
            "name": f"Coordinates ({x}, {y})",
            "kind": "map_coordinate",
            "region": region,
            "region_label": region_label,
            "coordinate_status": "verified",
            "coordinate_system": coordinate_system,
            "x": x,
            "y": y,
            "world_x": unreal.x(),
            "world_y": unreal.y(),
        }

    def set_coordinate_region(self, region: str) -> None:
        self.coordinate_region = region if region in {"palpagos", "world-tree"} else "unknown"
        if self.coordinate_region == "unknown":
            self.helper.setText("Type a waypoint or Alpha Pal. X/Y routing waits for a recognized regional map.")
        else:
            region_label = "World Tree" if self.coordinate_region == "world-tree" else "Palpagos"
            self.helper.setText(
                f"Type a waypoint, Alpha Pal, or X/Y on the current {region_label} map. Enter selects; Esc closes."
            )
        if hasattr(self, "search"):
            self._refresh_results(self.search.text())

    def set_live_unlocked_waypoint_keys(self, unlocked_keys: set[str] | None) -> None:
        self.live_unlocked_waypoint_keys = None if unlocked_keys is None else {
            str(key).upper() for key in unlocked_keys
        }
        self._refresh_results(self.search.text())

    def set_live_cleared_alpha_keys(self, cleared_keys: set[str] | None) -> None:
        self.live_cleared_alpha_keys = None if cleared_keys is None else {
            str(key).upper() for key in cleared_keys
        }
        self._refresh_results(self.search.text())

    def _live_unlock_state(self, location: dict) -> bool | None:
        if location.get("kind") != "fast_travel" or self.live_unlocked_waypoint_keys is None:
            return None
        upstream_key = str(location.get("upstream_key", "")).upper()
        if not upstream_key:
            return None
        return upstream_key in self.live_unlocked_waypoint_keys

    def _live_alpha_clear_state(self, location: dict) -> bool | None:
        if location.get("kind") != "alpha_pal" or self.live_cleared_alpha_keys is None:
            return None
        first_clear_key = str(location.get("first_clear_key", "")).upper()
        if not first_clear_key:
            return None
        return first_clear_key in self.live_cleared_alpha_keys

    def _matching_locations(self, query: str) -> list[dict]:
        normalized = " ".join(query.casefold().split())
        if not normalized:
            return sorted(
                self.locations,
                key=lambda location: (
                    self._live_unlock_state(location) is not True,
                    location["name"].casefold(),
                ),
            )

        tokens = normalized.split()

        def score(location: dict) -> float:
            candidates = [location["name"], *location.get("aliases", [])]
            normalized_candidates = [" ".join(value.casefold().split()) for value in candidates]
            if normalized in normalized_candidates:
                return 1.0
            if any(value.startswith(normalized) for value in normalized_candidates):
                return 0.97
            if any(normalized in value for value in normalized_candidates):
                return 0.94
            if all(any(token in value for value in normalized_candidates) for token in tokens):
                return 0.90
            if len(normalized) < 3:
                return 0.0
            phrase_similarity = max(
                difflib.SequenceMatcher(None, normalized, value).ratio()
                for value in normalized_candidates
            )
            word_similarity = sum(
                max(
                    difflib.SequenceMatcher(None, token, word).ratio()
                    for value in normalized_candidates
                    for word in value.split()
                )
                for token in tokens
            ) / len(tokens)
            return max(phrase_similarity, word_similarity * 0.92)

        ranked = [
            (score(location), location)
            for location in self.locations
        ]
        minimum_score = 0.58 if len(normalized) >= 5 else 0.68
        return [
            location
            for relevance, location in sorted(
                (item for item in ranked if item[0] >= minimum_score),
                key=lambda item: (
                    -item[0],
                    self._live_unlock_state(item[1]) is not True,
                    item[1]["name"].casefold(),
                ),
            )
        ]

    @Slot(str)
    def _refresh_results(self, query: str) -> None:
        parsed_coordinates = self.parse_coordinate_query(query)
        coordinate_target = self.coordinate_target(query, self.coordinate_region)
        matches = self._matching_locations(query)
        if coordinate_target is not None:
            matches.insert(0, coordinate_target)
            self.locations_by_id[coordinate_target["id"]] = coordinate_target
        self.results.clear()
        if parsed_coordinates is not None and self.coordinate_region == "unknown":
            item = QListWidgetItem("Coordinates need a recognized Palpagos or World Tree map")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.results.addItem(item)
            self.result_status.setText("Current regional map is unknown")
            return
        if parsed_coordinates is not None and coordinate_target is None:
            item = QListWidgetItem("Coordinates must be between -2500 and 2500")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.results.addItem(item)
            self.result_status.setText("Coordinate is outside the supported map range")
            return
        for location in matches[: self.MAX_VISIBLE_RESULTS]:
            is_watchtower = location.get("waypoint_class") == "watchtower"
            is_alpha_pal = location.get("kind") == "alpha_pal"
            is_coordinate = location.get("kind") == "map_coordinate"
            live_unlock_state = self._live_unlock_state(location)
            live_alpha_clear_state = self._live_alpha_clear_state(location)
            state_prefix = (
                "✓ "
                if live_unlock_state is True
                else "× " if live_unlock_state is False else ""
            )
            if is_coordinate:
                label = f"⌖ {location['x']}, {location['y']}  ·  {location['region_label']}"
            elif is_watchtower:
                label = f"{state_prefix}◆ {location['name']}"
            elif is_alpha_pal:
                level_min = location["level_min"]
                level_max = location["level_max"]
                level = str(level_min) if level_min == level_max else f"{level_min}–{level_max}"
                clear_prefix = (
                    "✓ " if live_alpha_clear_state is True
                    else "1ST " if live_alpha_clear_state is False
                    else ""
                )
                label = f"{clear_prefix}★ {location['name']}  ·  Lv {level}"
            else:
                label = f"{state_prefix}{location['name']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, location["id"])
            if is_coordinate:
                item.setToolTip(f"Route to pasted X/Y on the current {location['region_label']} map.")
            elif is_watchtower:
                state_text = (
                    " Runtime-confirmed unlocked."
                    if live_unlock_state is True
                    else " Runtime-confirmed locked." if live_unlock_state is False else ""
                )
                item.setToolTip(f"Higher-tier waypoint: reveals map coverage and supports transfer.{state_text}")
            elif is_alpha_pal:
                clear_text = (
                    " First clear confirmed from live player data."
                    if live_alpha_clear_state is True
                    else " First clear is still available in live player data."
                    if live_alpha_clear_state is False
                    else " First-clear state is unavailable."
                )
                item.setToolTip(f"Alpha Pal POI: always available in local search and routing.{clear_text}")
            elif live_unlock_state is not None:
                item.setToolTip(
                    "Runtime-confirmed unlocked."
                    if live_unlock_state
                    else "Runtime-confirmed locked."
                )
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)
        else:
            item = QListWidgetItem("No verified destination found")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.results.addItem(item)
        shown = min(len(matches), self.MAX_VISIBLE_RESULTS)
        self.result_status.setText(f"{shown} of {len(matches)} local suggestions")

    @Slot()
    def _choose_current(self) -> None:
        self._choose_item(self.results.currentItem())

    def _move_selection(self, delta: int) -> None:
        if not self.results.count():
            return
        next_row = max(0, min(self.results.count() - 1, self.results.currentRow() + delta))
        self.results.setCurrentRow(next_row)

    @Slot(QListWidgetItem)
    def _choose_item(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        location_id = item.data(Qt.ItemDataRole.UserRole)
        target = self.locations_by_id.get(location_id)
        if target is None:
            return
        self.select_callback(target)
        self.hide()

    @Slot()
    def _clear_destination(self) -> None:
        self.clear_callback()
        self.hide()

    def show_for(self, target: dict | None, anchor: QWidget, coordinate_region: str = "palpagos") -> None:
        self.set_coordinate_region(coordinate_region)
        if target is not None and target.get("kind") == "map_coordinate":
            self.search.setText(f"{target['x']}, {target['y']}")
        else:
            self.search.setText(target["name"] if target is not None else "")
        screen = QApplication.screenAt(anchor.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = area.right() - self.width() + 1
            below = anchor.frameGeometry().bottom() + 8
            y = below if below + self.height() <= area.bottom() else anchor.y() - self.height() - 8
            self.move(max(area.left(), x), max(area.top(), y))
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()
        self.search.selectAll()

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()


class CompanionWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.bundle = load_bundle()
        self.planner = Planner(self.bundle)
        self.store = Store(app_data_path())
        self.active_destination_ids: tuple[str, ...] = ()
        self.map_destination_id: str | None = None
        self.map_destination_target: dict | None = None
        self.path_overlay: PathOverlay | None = None
        self.destination_picker: DestinationPicker | None = None
        self._report_thread: QThread | None = None
        self._report_worker: DiagnosticReportWorker | None = None
        self.store.set_bundle_metadata(self.bundle["bundle_version"], self.bundle["game_version"])
        self.setWindowTitle("PalPlus 1.0.1")
        self.setMinimumSize(900, 600)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._build_ui()
        self._load_profile()
        self._load_map_destination()
        first_run = not self.store.is_onboarding_complete()
        self.welcome_panel.setVisible(first_run)
        self.quick_start_button.setVisible(not first_run)
        self._refresh_fast_travel()
        self._build_tray()
        QShortcut(QKeySequence("Ctrl+Alt+P"), self, activated=self.toggle_destination_picker)
        QShortcut(QKeySequence("Ctrl+Alt+M"), self, activated=self._toggle_path_overlay)

    def _build_ui(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self._build_welcome_panel())
        status = self.bundle["coverage"]
        travel_waypoints = [item for item in self.bundle["locations"] if item.get("kind") == "fast_travel"]
        watchtower_count = sum(item.get("waypoint_class") == "watchtower" for item in travel_waypoints)
        status_row = QHBoxLayout()
        self.coverage_label = QLabel(
            f"Local coverage: Core and Pure Quartz planning • {len(travel_waypoints)} travel waypoints"
            f" • {watchtower_count} map-reveal watchtowers"
        )
        self.coverage_label.setWordWrap(True)
        self.coverage_label.setObjectName("coverage")
        self.coverage_label.setToolTip(f"{status['status']}: {status['message']}")
        self.quick_start_button = QPushButton("Quick start")
        self.quick_start_button.clicked.connect(self._show_quick_start)
        self.report_problem_button = QPushButton("Report a problem")
        self.report_problem_button.clicked.connect(self._report_problem)
        status_row.addWidget(self.coverage_label, 1)
        status_row.addWidget(self.quick_start_button)
        status_row.addWidget(self.report_problem_button)
        layout.addLayout(status_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._check_in_panel())
        splitter.addWidget(self._map_panel())
        splitter.setSizes([360, 540])
        layout.addWidget(splitter)
        self.setCentralWidget(container)

    def _build_welcome_panel(self) -> QFrame:
        self.welcome_panel = QFrame()
        self.welcome_panel.setFrameShape(QFrame.Shape.StyledPanel)
        self.welcome_panel.setStyleSheet("QFrame { background: #eef5ff; border: 1px solid #9ab9df; border-radius: 7px; } QPushButton { padding: 5px 9px; }")
        layout = QVBoxLayout(self.welcome_panel)
        layout.setContentsMargins(12, 9, 12, 9)
        title = QLabel("Minimap is live")
        title.setStyleSheet("font-size: 16px; font-weight: 600; border: none")
        layout.addWidget(title)
        self.welcome_summary = QLabel(
            "Local first. No account, ads, writes, injection, automation, or background API calls. "
            "Diagnostic reports are sent only after you approve them."
        )
        self.welcome_summary.setStyleSheet("border: none")
        layout.addWidget(self.welcome_summary)
        self.welcome_instruction = QLabel(
            "The click-through minimap starts immediately in the top-right. No setup required. "
            "Type a destination to add a live bearing."
        )
        self.welcome_instruction.setStyleSheet("border: none")
        layout.addWidget(self.welcome_instruction)
        actions = QHBoxLayout()
        self.destination_quickstart = QPushButton("Choose destination")
        self.destination_quickstart.setDefault(True)
        self.destination_quickstart.setStyleSheet("font-weight: 600")
        self.destination_quickstart.clicked.connect(self._start_destination)
        self.core_quickstart = QPushButton("Plan Core farming")
        self.core_quickstart.clicked.connect(lambda: self._start_planning("Ancient Civilization Cores"))
        self.quartz_quickstart = QPushButton("Plan Pure Quartz")
        self.quartz_quickstart.clicked.connect(lambda: self._start_planning("Pure Quartz"))
        self.search_quickstart = QPushButton("Search knowledge")
        self.search_quickstart.clicked.connect(self._start_search)
        self.skip_welcome = QPushButton("Got it")
        self.skip_welcome.clicked.connect(self._dismiss_welcome)
        for button in (
            self.destination_quickstart,
            self.core_quickstart,
            self.quartz_quickstart,
            self.search_quickstart,
            self.skip_welcome,
        ):
            actions.addWidget(button)
        layout.addLayout(actions)
        tray_note = QLabel(
            "Optional: Delete opens compact destination search (Ctrl+Alt+P fallback); "
            "Ctrl+Alt+M toggles the minimap. Use the tray for the full planner."
        )
        tray_note.setStyleSheet("color: #52677f; border: none")
        layout.addWidget(tray_note)
        return self.welcome_panel

    def _check_in_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search local knowledge, e.g. ACC farm")
        self.search_box.returnPressed.connect(self.search_local_knowledge)
        search_button = QPushButton("Search")
        search_button.clicked.connect(self.search_local_knowledge)
        search_row.addWidget(self.search_box, 1)
        search_row.addWidget(search_button)
        layout.addLayout(search_row)
        form = QFormLayout()
        self.version = QLineEdit(self.bundle["game_version"])
        self.version.setReadOnly(True)
        self.level = QLineEdit()
        self.level.setPlaceholderText("Required")
        self.level.setValidator(QIntValidator(1, 999, self))
        self.level.textChanged.connect(self._suggest_tier)
        self.tier = QComboBox()
        self.tier.addItems(["early", "mid", "late", "endgame"])
        self.tier.setCurrentIndex(-1)
        self.tier.setPlaceholderText("Suggested from level; editable")
        self.goal = QLineEdit()
        self.goal.setPlaceholderText("e.g. improve travel or automation")
        self.bottleneck = QLineEdit()
        self.bottleneck.setPlaceholderText("Choose a quick start or type a covered topic")
        self.mounts = QLineEdit()
        self.mounts.setPlaceholderText("Optional, comma-separated")
        self.constraints = QLineEdit()
        form.addRow("Data version", self.version)
        form.addRow("Level", self.level)
        form.addRow("Tier", self.tier)
        form.addRow("What do you need?", self.bottleneck)
        layout.addLayout(form)
        self.optional_context_button = QPushButton("Optional context")
        self.optional_context_button.setCheckable(True)
        self.optional_context_button.toggled.connect(self._toggle_optional_context)
        layout.addWidget(self.optional_context_button)
        self.optional_context_panel = QWidget()
        optional_form = QFormLayout(self.optional_context_panel)
        optional_form.setContentsMargins(0, 0, 0, 0)
        optional_form.addRow("Broader goal", self.goal)
        optional_form.addRow("Mounts", self.mounts)
        optional_form.addRow("Constraints", self.constraints)
        self.optional_context_panel.hide()
        layout.addWidget(self.optional_context_panel)
        button = QPushButton("Generate session plan")
        button.clicked.connect(self.generate_plan)
        layout.addWidget(button)
        layout.addWidget(QLabel("Plan and evidence"))
        self.plan_view = QTextBrowser()
        self.plan_view.setOpenExternalLinks(True)
        layout.addWidget(self.plan_view, 2)
        layout.addWidget(QLabel("Unlocked fast travel (optional)"))
        self.fast_travel_count_label = QLabel()
        layout.addWidget(self.fast_travel_count_label)
        self.fast_travel_filter = QLineEdit()
        self.fast_travel_filter.setPlaceholderText("Filter fast travel")
        self.fast_travel_filter.textChanged.connect(self._refresh_fast_travel)
        layout.addWidget(self.fast_travel_filter)
        self.fast_travel_panel = QWidget()
        self.fast_travel_layout = QVBoxLayout(self.fast_travel_panel)
        self.fast_travel_layout.setContentsMargins(0, 0, 0, 0)
        self.fast_travel_scroll = QScrollArea()
        self.fast_travel_scroll.setWidgetResizable(True)
        self.fast_travel_scroll.setMaximumHeight(170)
        self.fast_travel_scroll.setWidget(self.fast_travel_panel)
        layout.addWidget(self.fast_travel_scroll)
        return panel

    def _load_profile(self) -> None:
        self.level.clear()
        self.tier.setCurrentIndex(-1)
        self.mounts.clear()
        profile = self.store.load_profile() or {}
        level = profile.get("level")
        if isinstance(level, int) and level >= 1:
            self.level.setText(str(level))
        tier = profile.get("tier")
        if tier in {"early", "mid", "late", "endgame"}:
            self.tier.setCurrentText(tier)
        mounts = profile.get("mounts")
        if isinstance(mounts, list):
            self.mounts.setText(", ".join(str(item) for item in mounts))

    def _load_map_destination(self) -> None:
        self.map_destination_id = None
        self.map_destination_target = None
        self.map_destination.setCurrentIndex(-1)
        self.path_status.setText("Live read-only minimap active. Choose a destination to add a straight-line path indicator.")
        custom_target = self.store.load_custom_map_destination()
        if custom_target is not None:
            self.map_destination_id = custom_target["id"]
            self.map_destination_target = custom_target
            self.map_destination.setCurrentText(f"{custom_target['x']}, {custom_target['y']}")
            self._update_destination_status(custom_target)
            self.map_view.set_map_html(self._map_html(self.active_destination_ids))
            return
        saved_id = self.store.load_map_destination()
        if saved_id is None:
            return
        locations = {item["id"]: item for item in self.bundle["locations"]}
        target = locations.get(saved_id)
        if not target or target.get("coordinate_status") != "verified":
            self.store.clear_map_destination()
            return
        self.map_destination_id = target["id"]
        self.map_destination_target = target
        self.map_destination.setCurrentText(target["name"])
        self._update_destination_status(target)
        self.map_view.set_map_html(self._map_html(self.active_destination_ids))

    def _save_profile(self) -> None:
        self.store.save_profile({
            "level": int(self.level.text()),
            "tier": self.tier.currentText(),
            "mounts": [item.strip() for item in self.mounts.text().split(",") if item.strip()],
        })

    def _suggest_tier(self, text: str) -> None:
        if not text:
            self.tier.setCurrentIndex(-1)
            return
        try:
            level = int(text)
        except ValueError:
            return
        if level >= 70:
            suggested = "endgame"
        elif level >= 52:
            suggested = "late"
        elif level >= 35:
            suggested = "mid"
        else:
            suggested = "early"
        self.tier.setCurrentText(suggested)

    def _finish_onboarding(self) -> None:
        self.store.set_onboarding_complete(True)
        self.welcome_panel.hide()
        self.quick_start_button.show()

    def _dismiss_welcome(self) -> None:
        self._finish_onboarding()
        self.hide()

    def _show_quick_start(self) -> None:
        self.welcome_panel.show()
        self.quick_start_button.hide()

    def _toggle_optional_context(self, visible: bool) -> None:
        self.optional_context_panel.setVisible(visible)
        self.optional_context_button.setText("Hide optional context" if visible else "Optional context")

    def _start_planning(self, bottleneck: str) -> None:
        self.bottleneck.setText(bottleneck)
        self.plan_view.setHtml(
            f"<h3>{html.escape(bottleneck)} selected</h3>"
            "<p>Next, enter your level. The suggested progression tier remains editable, then generate the plan.</p>"
        )
        self._finish_onboarding()
        self.level.setFocus()

    def _start_destination(self) -> None:
        self.path_status.setText("Start typing a fast-travel destination, then press Enter to show the live bearing.")
        self.map_destination.setFocus()
        self.map_destination.lineEdit().selectAll()

    def _start_search(self) -> None:
        self.plan_view.setHtml(
            "<h3>Search local knowledge</h3>"
            "<p>Try “ACC farm” or “Pure Quartz.” Unsupported topics say so directly.</p>"
        )
        self._finish_onboarding()
        self.search_box.setFocus()

    def search_local_knowledge(self) -> None:
        results = self.planner.search(self.search_box.text().strip())
        if not results:
            self.plan_view.setHtml("<h3>Not covered yet</h3><p>No current, verified local record matches that search.</p>")
            return
        html = ["<h3>Local knowledge</h3>"]
        for item in results:
            title = item.get("title", item.get("name", item["id"]))
            summary = item.get("summary", item.get("guidance", ""))
            html.append(f"<p><b>{title}</b><br>{summary}<br>{self._source_link(item['source_id'])}</p>")
        self.plan_view.setHtml("".join(html))

    def _map_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        heading = QLabel("Live minimap")
        heading.setStyleSheet("font-weight: 600; font-size: 16px")
        layout.addWidget(heading)
        self.path_status = QLabel("Live read-only minimap active. Choose a destination to add a straight-line path indicator.")
        self.path_status.setWordWrap(True)
        self.path_status.setStyleSheet("color: #52677f")
        layout.addWidget(self.path_status)
        path_row = QHBoxLayout()
        self.map_destination = self._path_combo("Type a waypoint or Alpha Pal")
        self.map_destination.activated.connect(self._set_map_destination)
        self.map_destination.lineEdit().returnPressed.connect(self._set_map_destination)
        clear_path = QPushButton("Clear destination")
        clear_path.clicked.connect(self._clear_map_destination)
        self.overlay_button = QPushButton("Overlay")
        self.overlay_button.setEnabled(True)
        self.overlay_button.clicked.connect(self._toggle_path_overlay)
        path_row.addWidget(QLabel("Path destination"))
        path_row.addWidget(self.map_destination, 1)
        path_row.addWidget(self.overlay_button)
        path_row.addWidget(clear_path)
        layout.addLayout(path_row)
        self.map_view = LocalMapView()
        self.map_view.set_map_html(self._map_html(()))
        layout.addWidget(self.map_view, 1)
        export_button = QPushButton("Export local progress")
        export_button.clicked.connect(self.export_state)
        import_button = QPushButton("Import local progress")
        import_button.clicked.connect(self.import_state)
        row = QHBoxLayout()
        row.addWidget(export_button)
        row.addWidget(import_button)
        layout.addLayout(row)
        return panel

    def _path_combo(self, placeholder: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        locations = self._verified_path_locations()
        for location in locations:
            combo.addItem(location["name"], location["id"])
        combo.setCurrentIndex(-1)
        combo.lineEdit().setPlaceholderText(placeholder)
        completer = QCompleter([location["name"] for location in locations], combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        combo.setCompleter(completer)
        return combo

    def _verified_path_locations(self) -> tuple[dict, ...]:
        return tuple(sorted(
            (
                item for item in self.bundle["locations"]
                if item.get("kind") in {"fast_travel", "alpha_pal"}
                and item.get("coordinate_status") == "verified"
            ),
            key=lambda item: item["name"].casefold(),
        ))

    def _resolve_path_location(self, text: str) -> dict | None:
        normalized = text.strip().casefold()
        if not normalized:
            return None
        map_locations = list(self.bundle["locations"])
        if self.map_destination_target is not None and self.map_destination_target.get("kind") == "map_coordinate":
            map_locations.append(self.map_destination_target)
        for location in map_locations:
            if location.get("kind") not in {"fast_travel", "alpha_pal"} or location.get("coordinate_status") != "verified":
                continue
            names = [location["name"], *location.get("aliases", [])]
            if normalized in {name.casefold() for name in names}:
                return location
        overlay = self._ensure_path_overlay()
        region = overlay.canvas.active_region
        return DestinationPicker.coordinate_target(text, region)

    def _set_map_destination(self, _index=None) -> None:
        target = self._resolve_path_location(self.map_destination.currentText())
        if target is None:
            QMessageBox.warning(self, "Unknown destination", "Choose a destination from the local verified list.")
            return
        self._apply_map_destination(target)

    def _apply_map_destination(self, target: dict) -> None:
        combo_index = self.map_destination.findData(target["id"])
        if combo_index >= 0:
            self.map_destination.setCurrentIndex(combo_index)
        elif target.get("kind") == "map_coordinate":
            self.map_destination.setCurrentText(f"{target['x']}, {target['y']}")
        else:
            self.map_destination.setCurrentText(target["name"])
        self.map_destination_id = target["id"]
        self.map_destination_target = target
        if target.get("kind") == "map_coordinate":
            self.store.save_custom_map_destination(target)
        else:
            self.store.save_map_destination(target["id"])
        self._update_destination_status(target)
        self.map_view.set_map_html(self._map_html(self.active_destination_ids))
        self._finish_onboarding()
        self._show_path_overlay()

    def _clear_map_destination(self) -> None:
        self.map_destination_id = None
        self.map_destination_target = None
        self.map_destination.setCurrentIndex(-1)
        self.store.clear_map_destination()
        self.path_status.setText("Live read-only minimap active. Choose a destination to add a straight-line path indicator.")
        if self.path_overlay is not None:
            self.path_overlay.set_destination(None)
        self.map_view.set_map_html(self._map_html(self.active_destination_ids))

    def _update_destination_status(self, target: dict) -> None:
        self.path_status.setText(f"Destination: {target['name']} • live bearing shown in the overlay")
        if self.path_overlay is not None:
            self.path_overlay.set_destination(target)

    def _ensure_path_overlay(self) -> PathOverlay:
        if self.path_overlay is None:
            self.path_overlay = PathOverlay(
                initial_zoom=self.store.load_minimap_zoom(),
                initial_alpha_pals_visible=self.store.load_map_layer_visibility("alpha_pals"),
                initial_anchor=OverlayAnchor(*self.store.load_overlay_anchor()),
            )
            self.path_overlay.zoom_changed.connect(self.store.save_minimap_zoom)
            self.path_overlay.alpha_pals_visibility_changed.connect(self._set_alpha_pals_layer_visible)
            self.path_overlay.position_changed.connect(self.store.save_overlay_position)
            self.path_overlay.anchor_changed.connect(self.store.save_overlay_anchor)
            self.path_overlay.set_landmarks(
                self._verified_path_locations(),
                self.store.unlocked_ids(),
            )
            self.destroyed.connect(self.path_overlay.deleteLater)
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.path_overlay.cleanup)
            if self.map_destination_target is not None:
                self._update_destination_status(self.map_destination_target)
        return self.path_overlay

    @Slot(bool)
    def _set_alpha_pals_layer_visible(self, visible: bool) -> None:
        self.store.save_map_layer_visibility("alpha_pals", bool(visible))
        self.map_view.set_map_html(self._map_html(self.active_destination_ids))

    def _current_map_destination(self) -> dict | None:
        return self.map_destination_target

    def _ensure_destination_picker(self) -> DestinationPicker:
        if self.destination_picker is None:
            self.destination_picker = DestinationPicker(
                self._verified_path_locations(),
                self._apply_map_destination,
                self._clear_map_destination,
            )
            self.destroyed.connect(self.destination_picker.deleteLater)
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.destination_picker.deleteLater)
        return self.destination_picker

    def toggle_destination_picker(self) -> None:
        picker = self._ensure_destination_picker()
        if picker.isVisible():
            picker.hide()
            return
        self._show_path_overlay()
        overlay = self._ensure_path_overlay()
        picker.set_live_unlocked_waypoint_keys(overlay.canvas.live_unlocked_waypoint_keys)
        picker.set_live_cleared_alpha_keys(overlay.canvas.live_cleared_alpha_keys)
        picker.show_for(
            self._current_map_destination(),
            overlay,
            coordinate_region=overlay.canvas.active_region,
        )

    def _overlay_position_is_visible(self, x: int, y: int) -> bool:
        return any(
            screen.availableGeometry().contains(x, y)
            for screen in QApplication.screens()
        )

    def _show_path_overlay(self) -> None:
        overlay = self._ensure_path_overlay()
        if not overlay.placed and not overlay.window_binding_enabled:
            saved_position = self.store.load_overlay_position()
            if saved_position is not None and self._overlay_position_is_visible(*saved_position):
                overlay.move(*saved_position)
                overlay.placed = True
            else:
                overlay.reset_to_default_position()
        overlay.show()
        self.overlay_button.setText("Hide overlay")

    def _toggle_path_overlay(self) -> None:
        overlay = self._ensure_path_overlay()
        if overlay.isVisible():
            overlay.hide()
            self.overlay_button.setText("Overlay")
        else:
            self._show_path_overlay()

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        icon_path = Path(__file__).with_name("assets") / "palplus-icon.png"
        app_icon = QIcon(str(icon_path))
        if app_icon.isNull():
            app_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(app_icon)
        self.tray.setIcon(app_icon)
        self.tray.setToolTip("PalPlus")
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        toggle = QAction("Show / hide companion", self)
        toggle.triggered.connect(self.toggle_visible)
        choose_destination = QAction("Choose destination (Delete)", self)
        choose_destination.triggered.connect(self.toggle_destination_picker)
        overlay_toggle = QAction("Show / hide minimap", self)
        overlay_toggle.triggered.connect(self._toggle_path_overlay)
        report_problem = QAction("Report a problem…", self)
        report_problem.triggered.connect(self._report_problem)
        self.tray_quit_action = QAction("Quit PalPlus", self)
        self.tray_quit_action.triggered.connect(self._quit_from_tray)
        menu.addAction(toggle)
        menu.addAction(choose_destination)
        menu.addAction(overlay_toggle)
        menu.addAction(report_problem)
        menu.addSeparator()
        menu.addAction(self.tray_quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    @Slot(QSystemTrayIcon.ActivationReason)
    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.toggle_visible()

    @Slot()
    def _quit_from_tray(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _confirm_diagnostic_report(self, report: dict) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Send diagnostic report")
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText("Send this redacted diagnostic report to PalPlus support?")
        dialog.setInformativeText(
            "This is optional. PalPlus sends nothing automatically. The report excludes player position, "
            "game saves, local paths, account details, and your local preferences."
        )
        dialog.setDetailedText(report_preview(report))
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return dialog.exec() == QMessageBox.StandardButton.Yes

    def _report_problem(self) -> None:
        if report_endpoint() is None:
            QMessageBox.information(
                self,
                "Diagnostic reports unavailable",
                "Diagnostic reporting is not configured in this build. You can still open a GitHub issue with a short description.",
            )
            return
        overlay = self.path_overlay
        report = build_diagnostic_report(
            live_status=overlay.live_status if overlay is not None else None,
            live_error=overlay.live_error if overlay is not None else None,
            map_error=overlay.map_provision_error if overlay is not None else None,
        )
        if not self._confirm_diagnostic_report(report):
            return
        self._send_diagnostic_report(report)

    def _send_diagnostic_report(self, report: dict) -> None:
        if self._report_thread is not None:
            return
        self.report_problem_button.setEnabled(False)
        self.report_problem_button.setText("Sending report…")
        thread = QThread(self)
        worker = DiagnosticReportWorker(report)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.sent.connect(lambda receipt: self._diagnostic_report_sent(report, receipt))
        worker.failed.connect(self._diagnostic_report_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._diagnostic_report_finished)
        self._report_thread = thread
        self._report_worker = worker
        thread.start()

    @Slot(object)
    def _diagnostic_report_sent(self, report: dict, receipt: dict) -> None:
        handoff = codex_handoff(report, receipt)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(handoff)
        QMessageBox.information(
            self,
            "Diagnostic report sent",
            f"Report {receipt['report_id']} was received. A short Codex handoff has been copied to your clipboard.",
        )

    @Slot(str)
    def _diagnostic_report_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Diagnostic report not sent", message)

    @Slot()
    def _diagnostic_report_finished(self) -> None:
        self._report_thread = None
        self._report_worker = None
        self.report_problem_button.setEnabled(True)
        self.report_problem_button.setText("Report a problem")

    def generate_plan(self) -> None:
        try:
            level = int(self.level.text())
        except ValueError:
            QMessageBox.warning(self, "Level needed", "Enter your current level.")
            self.level.setFocus()
            return
        if level < 1:
            QMessageBox.warning(self, "Invalid level", "Level must be at least 1.")
            self.level.setFocus()
            return
        if not self.tier.currentText():
            QMessageBox.warning(self, "Tier needed", "Choose a progression tier.")
            self.tier.setFocus()
            return
        check_in = CheckIn(
            game_version=self.version.text().strip(), level=level, tier=self.tier.currentText(),
            goal=self.goal.text().strip(), bottleneck=self.bottleneck.text().strip(),
            mounts=tuple(item.strip() for item in self.mounts.text().split(",") if item.strip()),
            constraints=self.constraints.text().strip(),
        )
        result = self.planner.plan(check_in)
        self._save_profile()
        self._finish_onboarding()
        self.store.save_plan(asdict(check_in), asdict(result))
        self.plan_view.setHtml(self._plan_html(result))
        destinations = result.primary.destination_ids if result.primary else ()
        self.active_destination_ids = destinations
        self.map_view.set_map_html(self._map_html(self.active_destination_ids))
        self._refresh_fast_travel()

    def _plan_html(self, result) -> str:
        if result.abstention_reason:
            matched = ", ".join(result.matched_rule_ids) or "none"
            return f"<h3>Recommendation withheld</h3><p>{result.abstention_reason}</p><p><b>Matched rules:</b> {matched}</p>"
        def block(label, item) -> str:
            sources = "<br>".join(self._source_link(source_id) for source_id in item.source_ids)
            return f"<h3>{label}: {item.title}</h3><p><b>Do:</b> {item.action}</p><p><b>Why:</b> {item.rationale}</p><p><b>Prerequisites:</b> {', '.join(item.prerequisites) or 'None recorded'}</p><p><b>Rule:</b> {item.rule_id}</p><p><b>Evidence:</b><br>{sources}</p>"
        html = block("Primary", result.primary)
        for index, alternative in enumerate(result.alternatives, 1):
            html += block(f"Alternative {index}", alternative)
        return html

    def _source_link(self, source_id: str) -> str:
        source = next(item for item in self.bundle["sources"] if item["id"] == source_id)
        return f'<a href="{source["url"]}">{source["name"]}</a> (checked {source["last_checked"]})'

    def _map_html(self, destination_ids: tuple[str, ...]) -> str:
        markers = []
        semantic = []
        unlocked = self.store.unlocked_ids()
        alpha_pals_visible = self.store.load_map_layer_visibility("alpha_pals")

        def map_percent(location: dict) -> tuple[float, float]:
            return (
                max(0, min(100, (location["x"] + 2000) / 30)),
                max(0, min(100, (1000 - location["y"]) / 30)),
            )

        for location in self.bundle["locations"]:
            selected = location["id"] in destination_ids
            is_unlocked_fast_travel = location["kind"] == "fast_travel" and location["id"] in unlocked
            is_alpha_pal = location["kind"] == "alpha_pal"
            is_map_destination = location["id"] == self.map_destination_id
            if not selected and not is_unlocked_fast_travel and not is_map_destination and not (is_alpha_pal and alpha_pals_visible):
                continue
            if location.get("region", "palpagos") != "palpagos":
                if selected or is_map_destination:
                    name = html.escape(location["name"])
                    semantic.append(
                        f'<div class="card"><b>{name}</b><br><span style="color:#aebed0">'
                        "World Tree destination. The live overlay switches to its separate regional map."
                        "</span></div>"
                    )
                continue
            if location["coordinate_status"] != "verified":
                name = html.escape(location["name"])
                guidance = html.escape(location.get("guidance", "No calibrated regional map coordinate"))
                semantic.append(f'<div style="margin:8px 12px;padding:10px;border:1px solid #ffb454;border-radius:6px;background:#17202b"><b>{name}</b><br><span style="color:#aebed0">{guidance}</span></div>')
                continue
            x, y = map_percent(location)
            if is_map_destination:
                color = "#ffb454"
            elif selected:
                color = "#ffb454"
            elif is_alpha_pal:
                color = "#ff6f91"
            else:
                color = "#4da3ff"
            name = html.escape(location["name"], quote=True)
            if is_alpha_pal:
                marker_class = "marker alpha-pal"
                tier_note = f" · Alpha Pal · level {location['level_min']}"
            elif location.get("waypoint_class") == "watchtower":
                marker_class = "marker watchtower"
                tier_note = " · map-reveal watchtower"
            else:
                marker_class = "marker"
                tier_note = ""
            markers.append(f'<span class="{marker_class}" title="{name}{tier_note} ({location["x"]}, {location["y"]})" aria-label="{name}" style="left:{x}%;top:{y}%;background:{color}"></span>')
        marker_html = "".join(markers)
        semantic_html = "".join(semantic)
        if not marker_html and not semantic_html:
            semantic_html = '<p style="padding:18px">No unlocked fast-travel nodes or fixed plan destination. Mark nodes on the left to add them.</p>'
        return f"""<!doctype html><html><head><meta charset='utf-8'><style>
        html,body,#viewport{{width:100%;height:100%;margin:0;overflow:hidden;background:#10151d;color:#dbe7f5;font-family:Segoe UI,sans-serif}}
        #viewport{{position:relative;cursor:grab;touch-action:none}}
        #world{{position:absolute;width:900px;height:900px;transform-origin:0 0;background-image:linear-gradient(#27364a 1px,transparent 1px),linear-gradient(90deg,#27364a 1px,transparent 1px);background-size:10% 10%}}
        .legend{{position:absolute;z-index:4;left:12px;top:10px;padding:6px 9px;border-radius:5px;background:#10151ddd;color:#8fa4bd;font-size:12px}}
        .marker{{position:absolute;z-index:2;width:16px;height:18px;margin:-8px;clip-path:polygon(8% 0,35% 0,35% 24%,65% 24%,65% 0,92% 0,92% 72%,100% 72%,100% 100%,0 100%,0 72%,8% 72%);border:0;box-shadow:0 0 0 2px #10151d,0 1px 5px #000;cursor:help}}
        .marker.watchtower{{width:15px;height:15px;margin:-8px;clip-path:none;border-radius:2px;border:3px double white;transform:rotate(45deg);box-shadow:0 0 0 2px #10151d,0 1px 7px #000}}
        .marker.alpha-pal{{z-index:3;width:19px;height:19px;margin:-9px;clip-path:polygon(50% 0,61% 35%,98% 35%,68% 56%,79% 94%,50% 71%,21% 94%,32% 56%,2% 35%,39% 35%);border:0;box-shadow:0 0 0 2px #10151d,0 1px 7px #000}}
        .cards{{padding-top:42px;max-width:420px}} .card{{margin:8px 12px;padding:10px;border:1px solid #ffb454;border-radius:6px;background:#17202b}}
        </style></head><body><div id='viewport'><div class='legend'>gold destination · tower waypoint · diamond watchtower · star Alpha Pal · wheel to zoom · drag to pan</div>
        <div id='world'><div class='cards'>{semantic_html}</div>{marker_html}</div></div><script>
        const viewport=document.getElementById('viewport'), world=document.getElementById('world');
        let scale=.62, tx=18, ty=42, dragging=false, sx=0, sy=0;
        function draw(){{world.style.transform=`translate(${{tx}}px,${{ty}}px) scale(${{scale}})`}}
        viewport.addEventListener('wheel',e=>{{e.preventDefault();const old=scale;scale=Math.max(.35,Math.min(3,scale*(e.deltaY<0?1.15:.87)));const r=viewport.getBoundingClientRect();const mx=e.clientX-r.left,my=e.clientY-r.top;tx=mx-(mx-tx)*scale/old;ty=my-(my-ty)*scale/old;draw()}},{{passive:false}});
        viewport.addEventListener('pointerdown',e=>{{dragging=true;sx=e.clientX-tx;sy=e.clientY-ty;viewport.setPointerCapture(e.pointerId);viewport.style.cursor='grabbing'}});
        viewport.addEventListener('pointermove',e=>{{if(dragging){{tx=e.clientX-sx;ty=e.clientY-sy;draw()}}}});
        viewport.addEventListener('pointerup',()=>{{dragging=false;viewport.style.cursor='grab'}});draw();
        </script></body></html>"""

    def _refresh_fast_travel(self) -> None:
        while self.fast_travel_layout.count():
            child = self.fast_travel_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        fast_travel = sorted(
            (item for item in self.bundle["locations"] if item["kind"] == "fast_travel"),
            key=lambda item: (
                item["coordinate_status"] != "verified",
                item.get("waypoint_class") != "watchtower",
                item["name"].lower(),
            ),
        )
        watchtower_count = sum(item.get("waypoint_class") == "watchtower" for item in fast_travel)
        self.fast_travel_count_label.setText(
            f"{len(fast_travel)} travel waypoints, including {watchtower_count} map-reveal watchtowers; "
            "check the ones unlocked in your world."
        )
        if not fast_travel:
            self.fast_travel_layout.addWidget(QLabel("No audited fast-travel nodes in the current bundle."))
            return
        query = self.fast_travel_filter.text().strip().lower()
        visible = [item for item in fast_travel if not query or query in item["name"].lower()]
        unlocked = self.store.unlocked_ids()
        for location in visible:
            label = (
                f"◆ {location['name']} · reveals map"
                if location.get("waypoint_class") == "watchtower"
                else location["name"]
            )
            checkbox = QCheckBox(label)
            if location.get("waypoint_class") == "watchtower":
                checkbox.setToolTip("Higher-tier waypoint: reveals map coverage and supports transfer.")
            checkbox.setChecked(location["id"] in unlocked)
            checkbox.toggled.connect(lambda checked, location_id=location["id"]: self._toggle_fast_travel(location_id, checked))
            self.fast_travel_layout.addWidget(checkbox)

    def _toggle_fast_travel(self, location_id: str, checked: bool) -> None:
        self.store.set_unlocked(location_id, checked)
        if self.path_overlay is not None:
            self.path_overlay.set_landmarks(self.path_overlay.canvas.landmarks, self.store.unlocked_ids())
        self.map_view.set_map_html(self._map_html(self.active_destination_ids))

    def export_state(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export progress", "palworld-companion-progress.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.store.export_state(), indent=2), encoding="utf-8")

    def import_state(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import progress", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.store.import_state(json.loads(Path(path).read_text(encoding="utf-8")), {item["id"] for item in self.bundle["locations"]})
            self._load_profile()
            self._load_map_destination()
            self._refresh_fast_travel()
            self.map_view.set_map_html(self._map_html(self.active_destination_ids))
        except (ValueError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "Import failed", str(error))

    def toggle_visible(self) -> None:
        self.setVisible(not self.isVisible())
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            if self.map_destination_id is None and not self.store.is_onboarding_complete():
                QTimer.singleShot(0, self._start_destination)

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = CompanionWindow()
    window._ensure_path_overlay()
    global_hotkey = WindowsGlobalHotkey(window.toggle_destination_picker, window._toggle_path_overlay)
    hotkey_context_timer = QTimer()
    hotkey_context_timer.setInterval(250)
    hotkey_context_timer.timeout.connect(global_hotkey.refresh_contextual_hotkeys)
    hotkey_context_timer.start()
    app.installNativeEventFilter(global_hotkey)
    app.aboutToQuit.connect(hotkey_context_timer.stop)
    app.aboutToQuit.connect(global_hotkey.close)
    place_on_requested_monitor(app, window)
    QTimer.singleShot(0, window._show_path_overlay)
    sys.exit(app.exec())
