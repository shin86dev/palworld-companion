import json

import pytest

from palworld_companion import diagnostics
from support_server.report_server import ReportStore, validate_report


def test_report_is_fixed_shape_and_redacts_local_identifiers(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / "PalworldCompanion"
    root.mkdir()
    (root / "startup-error.log").write_text(
        "Traceback\nC:\\Users\\Alice\\PalPlus\\app.py failed for alice@example.com at 192.0.2.1",
        encoding="utf-8",
    )

    report = diagnostics.build_report(
        live_status="paused",
        live_error="C:\\Users\\Alice\\Palworld error from alice@example.com",
        map_error="no map",
    )
    encoded = json.dumps(report)

    assert report["schema_version"] == 1
    assert report["context"]["live_status"] == "paused"
    assert "Alice" not in encoded
    assert "alice@example.com" not in encoded
    assert "192.0.2.1" not in encoded
    assert "position" not in report["context"]
    assert "save" not in encoded.casefold()


def test_endpoint_is_opt_in_and_requires_https(monkeypatch):
    monkeypatch.delenv("PALPLUS_REPORT_URL", raising=False)
    monkeypatch.setattr(diagnostics, "_bundled_endpoint", lambda: "")
    assert diagnostics.report_endpoint() is None

    monkeypatch.setenv("PALPLUS_REPORT_URL", "http://example.test/v1/reports")
    assert diagnostics.report_endpoint() is None

    monkeypatch.setenv("PALPLUS_REPORT_URL", "https://reports.example.test/v1/reports")
    assert diagnostics.report_endpoint() == "https://reports.example.test/v1/reports"


def test_submit_makes_one_explicit_post_and_returns_the_receipt(monkeypatch):
    report = diagnostics.build_report(live_error="reader paused")
    seen = {}

    class Response:
        def read(self, _size):
            return json.dumps({"report_id": report["report_id"]}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_request(request, *, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.method
        seen["body"] = json.loads(request.data)
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(diagnostics.urllib.request, "urlopen", open_request)
    receipt = diagnostics.submit_report(report, endpoint="https://reports.example.test/v1/reports")

    assert receipt["report_id"] == report["report_id"]
    assert seen["method"] == "POST"
    assert seen["body"] == report
    assert "Codex" in diagnostics.codex_handoff(report, receipt)


def test_receiver_accepts_only_the_fixed_schema_and_persists_no_connection_data(tmp_path):
    report = diagnostics.build_report(live_error="reader paused")
    accepted = validate_report(report)
    store = ReportStore(tmp_path / "reports.sqlite3")
    received_at = store.insert(accepted)
    rows = store.list_reports()

    assert received_at.endswith("+00:00")
    assert rows[0]["report_id"] == report["report_id"]
    assert set(rows[0]) == {"report_id", "received_at_utc", "report"}

    invalid = dict(report, unexpected="nope")
    with pytest.raises(ValueError, match="Unexpected report fields"):
        validate_report(invalid)
