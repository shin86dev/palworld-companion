"""Render deterministic PalPlus release imagery from the shipped UI primitives."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets"
PACKAGE_ASSET_DIR = ROOT / "src" / "palworld_companion" / "assets"
SCREENSHOT_DIR = ROOT / "docs" / "images"


def render_icon() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    PACKAGE_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    size = 512
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((28, 28, 484, 484), radius=104, fill="#0b1722", outline="#3d8bff", width=18)
    draw.ellipse((116, 116, 396, 396), outline="#6eb7ff", width=22)
    draw.line((256, 92, 256, 420), fill="#31506b", width=12)
    draw.line((92, 256, 420, 256), fill="#31506b", width=12)
    draw.polygon(((256, 126), (312, 292), (256, 270), (200, 292)), fill="#ffd21f", outline="#f6fbff")
    draw.ellipse((225, 225, 287, 287), fill="#3d8bff", outline="#f6fbff", width=8)
    png_path = PACKAGE_ASSET_DIR / "palplus-icon.png"
    image.resize((256, 256), Image.Resampling.LANCZOS).save(png_path, optimize=True)
    image.save(
        ASSET_DIR / "palplus.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def render_screenshot() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from palworld_companion.app import PathOverlay
    from palworld_companion.bundle import load_bundle

    app = QApplication.instance() or QApplication([])
    bundle = load_bundle()
    landmarks = tuple(
        location for location in bundle["locations"]
        if location.get("coordinate_status") == "verified"
    )
    target = next(location for location in landmarks if location.get("region") == "palpagos")
    sample = {
        "position": {
            "x": float(target.get("world_x", 0)),
            "y": float(target.get("world_y", 0)),
            "z": 0.0,
        },
        "heading_degrees": 35.0,
    }
    overlay = PathOverlay()
    if os.environ.get("PALPLUS_RELEASE_SCREENSHOT_WITH_LOCAL_MAP") != "1":
        # Keep routine local renders free of installed-game textures unless a release author
        # explicitly opts in to a representative screenshot.
        overlay.canvas.map_image = QImage()
        overlay.canvas.tree_map_image = QImage()
    overlay.set_landmarks(landmarks)
    overlay.set_destination(target)
    overlay.canvas.set_live_sample(sample)
    overlay.live_status = "Read-only local preview"
    overlay._refresh_disclaimer()
    overlay.show()
    app.processEvents()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    overlay.grab().save(str(SCREENSHOT_DIR / "palplus-overlay.png"))
    overlay.hide()
    overlay.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    render_icon()
    render_screenshot()
