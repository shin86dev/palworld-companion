"""Explicit, privacy-limited diagnostic reports.

Nothing in this module runs on a timer or during startup.  A report is built
and sent only after the person using PalPlus confirms the preview in the UI.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import __version__


REPORT_SCHEMA_VERSION = 1
REQUEST_TIMEOUT_SECONDS = 10
MAX_TEXT_LENGTH = 1_200


class DiagnosticSubmissionError(RuntimeError):
    """A report could not be submitted without retrying in the background."""


def app_data_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local")) / "PalworldCompanion"


def redact_text(value: object, *, limit: int = MAX_TEXT_LENGTH) -> str:
    """Keep error meaning while removing common identifiers and local paths."""
    text = str(value).replace("\x00", " ").strip()
    text = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "<email>", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip-address>", text)
    text = re.sub(r"(?i)(?:[A-Z]:\\|\\\\)[^\r\n]*", "<local-path>", text)
    text = re.sub(r"/(?:Users|home)/[^\s:]+", "<local-path>", text)
    text = re.sub(r"(?i)steamid(?:64)?[=: ]+\d+", "steamid=<redacted>", text)
    return text[:limit]


def startup_error_summary() -> str | None:
    """Return only the redacted tail of the local startup log, if one exists."""
    path = app_data_directory() / "startup-error.log"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    tail = "\n".join(line for line in lines[-8:] if line.strip())
    return redact_text(tail) if tail else None


def _bundled_endpoint() -> str:
    try:
        raw = files("palworld_companion").joinpath("assets", "reporting.json").read_text(encoding="utf-8")
        value = json.loads(raw).get("endpoint", "")
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        return ""
    return value if isinstance(value, str) else ""


def report_endpoint() -> str | None:
    """Read an endpoint without ever treating a missing endpoint as an error."""
    endpoint = os.environ.get("PALPLUS_REPORT_URL", _bundled_endpoint()).strip()
    if not endpoint:
        return None
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return endpoint


def build_report(
    *,
    live_status: str | None = None,
    live_error: str | None = None,
    map_error: str | None = None,
) -> dict[str, Any]:
    """Build the fixed, inspectable report shape shown to the user before send."""
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_id": str(uuid.uuid4()),
        "created_at_utc": datetime.now(UTC).isoformat(),
        "app_version": __version__,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "architecture": platform.architecture()[0],
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        },
        "context": {
            "live_status": redact_text(live_status or "not available", limit=240),
            "live_error": redact_text(live_error, limit=MAX_TEXT_LENGTH) if live_error else None,
            "map_error": redact_text(map_error, limit=MAX_TEXT_LENGTH) if map_error else None,
        },
        "startup_error_summary": startup_error_summary(),
    }


def report_preview(report: dict[str, Any]) -> str:
    """A human-readable preview for the consent dialog; no hidden fields exist."""
    return json.dumps(report, indent=2, sort_keys=True)


def codex_handoff(report: dict[str, Any], receipt: dict[str, Any] | None = None) -> str:
    report_id = str((receipt or {}).get("report_id") or report["report_id"])
    context = report["context"]
    problem = context.get("live_error") or context.get("map_error") or context.get("live_status")
    return (
        f"PalPlus diagnostic report ID: {report_id}\n"
        f"App version: {report['app_version']}\n"
        f"Observed state: {problem}\n"
        "Paste this into Codex with the report preview to investigate the issue."
    )


def submit_report(report: dict[str, Any], *, endpoint: str | None = None) -> dict[str, Any]:
    """Make one explicit HTTPS request. Failures are returned to the person, never queued."""
    destination = endpoint or report_endpoint()
    if destination is None:
        raise DiagnosticSubmissionError("Diagnostic reporting is not configured in this build.")
    body = json.dumps(report, separators=(",", ":"), sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        destination,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"PalPlus/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read(4_096)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DiagnosticSubmissionError(f"Could not send the report: {redact_text(error, limit=240)}") from error
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DiagnosticSubmissionError("The diagnostic service returned an invalid response.") from error
    if not isinstance(receipt, dict) or not isinstance(receipt.get("report_id"), str):
        raise DiagnosticSubmissionError("The diagnostic service did not return a report ID.")
    return receipt
