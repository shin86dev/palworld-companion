# PalPlus 1.0.1

PalPlus 1.0.1 adds an optional, user-approved diagnostic-report path to the local, ad-free Windows minimap and planning companion.

## Highlights

- Live read-only player position, heading, waypoint state, and Alpha first-clear state.
- Private local Palpagos and World Tree map extraction with a coordinate-grid fallback.
- Fuzzy waypoint and Alpha Pal destination search, pasted coordinate routing, zoom, and persistent layers.
- Window-bound minimap placement across game moves, resize, monitor/DPI changes, minimize, and restore.
- Deterministic local executable-update audit that remains fail-closed when validation is incomplete.
- Optional diagnostic reports: preview the exact redacted payload, approve it explicitly, and get a report ID plus a Codex handoff. Nothing is sent in the background.

## Install

Download `PalPlus-Setup.exe` and `PalPlus-Setup.exe.sha256` from this release. The installer is unsigned and per-user, requires no elevation, and preserves personal state when uninstalled. Verify the installer checksum before running it.

Approved diagnostic reports contain only the app version, Windows version, and sanitized error state. They exclude player position, saves, preferences, local paths, account details, and game files.

PalPlus is an independent community project and is not affiliated with or endorsed by Pocketpair.
