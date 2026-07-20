"""Small, dependency-free Railway receiver for opt-in PalPlus reports.

The public endpoint accepts only a tightly validated report schema. It does not
store IP addresses, user agents, player state, game saves, or local paths.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


MAX_BODY_BYTES = 12_000
MAX_TEXT_LENGTH = 1_200
RATE_LIMIT = 5
RATE_WINDOW_SECONDS = 3_600
EXPECTED_KEYS = {
    "schema_version", "report_id", "created_at_utc", "app_version", "platform", "context", "startup_error_summary",
}


def database_path() -> Path:
    explicit = os.environ.get("PALPLUS_REPORT_DB")
    if explicit:
        return Path(explicit)
    mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    return Path(mount) / "palplus-reports.sqlite3"


def validate_report(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != EXPECTED_KEYS:
        raise ValueError("Unexpected report fields")
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported report schema")
    try:
        uuid.UUID(str(payload["report_id"]))
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError("Invalid report ID") from error
    for key in ("created_at_utc", "app_version"):
        if not isinstance(payload.get(key), str) or len(payload[key]) > 128:
            raise ValueError(f"Invalid {key}")
    platform = payload.get("platform")
    context = payload.get("context")
    if not isinstance(platform, dict) or set(platform) != {"system", "release", "architecture", "python"}:
        raise ValueError("Invalid platform")
    if not isinstance(context, dict) or set(context) != {"live_status", "live_error", "map_error"}:
        raise ValueError("Invalid context")
    for group in (platform, context):
        for value in group.values():
            if value is not None and (not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH):
                raise ValueError("Invalid report text")
    summary = payload.get("startup_error_summary")
    if summary is not None and (not isinstance(summary, str) or len(summary) > MAX_TEXT_LENGTH):
        raise ValueError("Invalid startup error summary")
    return payload


class ReportStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS reports (report_id TEXT PRIMARY KEY, received_at_utc TEXT NOT NULL, payload_json TEXT NOT NULL)"
        )
        self.connection.commit()
        self.lock = threading.Lock()

    def insert(self, payload: dict[str, Any]) -> str:
        received_at = datetime.now(UTC).isoformat()
        with self.lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO reports(report_id, received_at_utc, payload_json) VALUES (?, ?, ?)",
                (payload["report_id"], received_at, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
            self.connection.commit()
        return received_at

    def list_reports(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT report_id, received_at_utc, payload_json FROM reports ORDER BY received_at_utc DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {"report_id": row[0], "received_at_utc": row[1], "report": json.loads(row[2])}
            for row in rows
        ]


class RateLimiter:
    def __init__(self) -> None:
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def permit(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            events = self.events[key]
            while events and now - events[0] >= RATE_WINDOW_SECONDS:
                events.popleft()
            if len(events) >= RATE_LIMIT:
                return False
            events.append(now)
            return True


def make_handler(store: ReportStore, limiter: RateLimiter, admin_token: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PalPlusReportReceiver/1"

        def log_message(self, _format: str, *_args: object) -> None:
            """Do not write request IPs or user agents to application logs."""

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _admin(self) -> bool:
            if not admin_token:
                return False
            return self.headers.get("Authorization") == f"Bearer {admin_token}"

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok"})
                return
            if self.path == "/v1/reports":
                if not self._admin():
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                self._json(HTTPStatus.OK, {"reports": store.list_reports()})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/reports":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not limiter.permit(self.client_address[0]):
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "try again later"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = 0
            if content_length <= 0 or content_length > MAX_BODY_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "invalid report size"})
                return
            try:
                payload = validate_report(json.loads(self.rfile.read(content_length).decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                return
            received_at = store.insert(payload)
            self._json(HTTPStatus.CREATED, {"report_id": payload["report_id"], "received_at_utc": received_at})

    return Handler


def main() -> None:
    path = database_path()
    store = ReportStore(path)
    server = ThreadingHTTPServer(
        ("0.0.0.0", int(os.environ.get("PORT", "8080"))),
        make_handler(store, RateLimiter(), os.environ.get("PALPLUS_REPORT_ADMIN_TOKEN")),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
