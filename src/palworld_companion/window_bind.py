from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


PALWORLD_PROCESS_NAMES = {"palworld.exe", "palworld-win64-shipping.exe"}
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


@dataclass(frozen=True)
class GameWindowState:
    rect: WindowRect
    minimized: bool = False
    foreground: bool = True

    @property
    def displayable(self) -> bool:
        return not self.minimized and self.foreground and self.rect.width > 0 and self.rect.height > 0


@dataclass(frozen=True)
class OverlayAnchor:
    x_ratio: float = 1.0
    y_ratio: float = 0.0

    def clamped(self) -> "OverlayAnchor":
        return OverlayAnchor(
            max(0.0, min(1.0, float(self.x_ratio))),
            max(0.0, min(1.0, float(self.y_ratio))),
        )


def overlay_position(
    rect: WindowRect,
    overlay_size: tuple[int, int],
    anchor: OverlayAnchor,
    *,
    margin: int = 20,
) -> tuple[int, int]:
    """Resolve a normalized client-relative anchor into screen coordinates."""
    width, height = overlay_size
    left = rect.left + min(margin, max(0, (rect.width - width) // 2))
    top = rect.top + min(margin, max(0, (rect.height - height) // 2))
    right = max(left, rect.right - width - margin)
    bottom = max(top, rect.bottom - height - margin)
    normalized = anchor.clamped()
    return (
        round(left + (right - left) * normalized.x_ratio),
        round(top + (bottom - top) * normalized.y_ratio),
    )


def overlay_anchor(
    rect: WindowRect,
    overlay_size: tuple[int, int],
    position: tuple[int, int],
    *,
    margin: int = 20,
) -> OverlayAnchor:
    """Convert a dragged screen position back to normalized client-relative state."""
    width, height = overlay_size
    left = rect.left + min(margin, max(0, (rect.width - width) // 2))
    top = rect.top + min(margin, max(0, (rect.height - height) // 2))
    right = max(left, rect.right - width - margin)
    bottom = max(top, rect.bottom - height - margin)
    x_ratio = 0.0 if right == left else (position[0] - left) / (right - left)
    y_ratio = 0.0 if bottom == top else (position[1] - top) / (bottom - top)
    return OverlayAnchor(x_ratio, y_ratio).clamped()


class WindowsPalworldWindowProbe:
    """Find the visible Palworld client window without opening or modifying the process."""

    def __init__(self) -> None:
        self.available = sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") != "offscreen"
        if not self.available:
            return
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.ClientToScreen.restype = wintypes.BOOL
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

    def _process_name(self, hwnd: int) -> str | None:
        process_id = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return None
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32_768)
            size = wintypes.DWORD(len(buffer))
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return Path(buffer.value).name.casefold()
        finally:
            self.kernel32.CloseHandle(handle)

    def __call__(self) -> GameWindowState | None:
        if not self.available:
            return None
        candidates: list[tuple[int, WindowRect, bool]] = []
        foreground = int(self.user32.GetForegroundWindow() or 0)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def inspect(hwnd, _lparam):
            if not self.user32.IsWindowVisible(hwnd):
                return True
            if self._process_name(hwnd) not in PALWORLD_PROCESS_NAMES:
                return True
            client = wintypes.RECT()
            origin = wintypes.POINT(0, 0)
            if not self.user32.GetClientRect(hwnd, ctypes.byref(client)):
                return True
            if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
                return True
            rect = WindowRect(
                int(origin.x),
                int(origin.y),
                int(origin.x + client.right - client.left),
                int(origin.y + client.bottom - client.top),
            )
            candidates.append((int(hwnd), rect, bool(self.user32.IsIconic(hwnd))))
            return True

        self.user32.EnumWindows(inspect, 0)
        if not candidates:
            return None
        hwnd, rect, minimized = max(candidates, key=lambda item: item[1].width * item[1].height)
        return GameWindowState(rect=rect, minimized=minimized, foreground=hwnd == foreground)
