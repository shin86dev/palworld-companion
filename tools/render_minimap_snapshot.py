"""Render the exact live minimap widget to a PNG for visual acceptance checks."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from palworld_companion.app import PathOverlay
from palworld_companion.bundle import load_bundle
from palworld_companion.telemetry import PalworldLiveReader


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--destination")
    arguments = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    overlay = PathOverlay()
    bundle = load_bundle()
    overlay.set_landmarks(
        tuple(
            item
            for item in bundle["locations"]
            if item.get("kind") == "fast_travel" and item.get("coordinate_status") == "verified"
        )
    )
    if arguments.destination:
        normalized = arguments.destination.casefold()
        destination = next(
            (
                item
                for item in bundle["locations"]
                if item["name"].casefold() == normalized
            ),
            None,
        )
        if destination is None:
            parser.error(f"Unknown destination: {arguments.destination}")
        overlay.set_destination(destination)

    overlay.show()
    app.processEvents()
    overlay.live_reader = PalworldLiveReader()
    overlay._poll_live_telemetry()
    overlay.repaint()
    app.processEvents()

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    if not overlay.grab().save(str(arguments.output)):
        raise RuntimeError(f"Could not save minimap snapshot to {arguments.output}")
    print(arguments.output.resolve())
    overlay.cleanup()
    overlay.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
