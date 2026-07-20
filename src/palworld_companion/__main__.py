from __future__ import annotations

import ctypes
import json
import os
import sys
import traceback
import uuid
from pathlib import Path


def _report_startup_error(*, show_dialog: bool = True) -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local")) / "PalworldCompanion"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "startup-error.log"
    path.write_text(traceback.format_exc(), encoding="utf-8")
    if show_dialog and sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(
            None,
            f"PalPlus could not start.\n\nDetails were written to:\n{path}",
            "PalPlus startup error",
            0x10,
        )
    return path


def run(arguments: list[str] | None = None) -> int:
    """Run the installed executable or one explicit diagnostic command."""
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    command = arguments[0].casefold() if arguments else None

    if command == "--check":
        from .bundle import load_bundle
        from .pak_reader import _load_pak_file_class

        bundle = load_bundle()
        _load_pak_file_class()
        print(f"PalPlus ready, bundle {bundle['bundle_version']}, local map reader available")
        return 0
    if command == "--telemetry-check":
        from .telemetry import probe_palworld

        print(json.dumps(probe_palworld(auto_audit=True), indent=2))
        return 0
    if command in {"--map-check", "--map-provision"}:
        from .map_asset import (
            MapAssetError,
            find_palworld_pak,
            map_provision_diagnostics,
            provision_tree_map,
            provision_world_map,
        )

        if command == "--map-provision":
            pak_path = find_palworld_pak()
            if pak_path is None:
                print(json.dumps({
                    **map_provision_diagnostics(),
                    "provision_error": "Palworld's installed Steam archive was not found.",
                }, indent=2))
                return 1
            try:
                provision_world_map(pak_path)
                provision_tree_map(pak_path)
            except MapAssetError as error:
                print(json.dumps({
                    **map_provision_diagnostics(),
                    "provision_error": str(error),
                }, indent=2))
                return 1
        print(json.dumps(map_provision_diagnostics(), indent=2))
        return 0
    if command == "--first-launch-check":
        from .first_launch_check import main as first_launch_main

        return first_launch_main()
    if command == "--fresh":
        state_name = f"PalPlusFresh-{uuid.uuid4().hex}.sqlite3"
        os.environ["PALPLUS_STATE_PATH"] = str(Path(os.environ.get("TEMP", ".")) / state_name)
        os.environ["PALPLUS_MONITOR"] = "secondary"
        arguments.pop(0)
        command = arguments[0].casefold() if arguments else None
    if command == "--foreground":
        arguments.pop(0)
    if arguments:
        raise ValueError(f"Unknown PalPlus option: {arguments[0]}")

    from .app import main as app_main

    app_main()
    return 0


def main() -> int:
    try:
        return run()
    except Exception:
        _report_startup_error(show_dialog=not bool(sys.argv[1:]))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
