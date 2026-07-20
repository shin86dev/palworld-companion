import json
from pathlib import Path

from palworld_companion import __main__ as entrypoint


def test_check_command_validates_the_bundled_data(capsys):
    assert entrypoint.run(["--check"]) == 0
    output = capsys.readouterr().out
    assert "PalPlus ready, bundle" in output
    assert "local map reader available" in output


def test_telemetry_command_requests_local_auto_audit(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        "palworld_companion.telemetry.probe_palworld",
        lambda **kwargs: calls.append(kwargs) or {"status": "game_not_running"},
    )
    assert entrypoint.run(["--telemetry-check"]) == 0
    assert calls == [{"auto_audit": True}]
    assert json.loads(capsys.readouterr().out)["status"] == "game_not_running"


def test_fresh_launch_uses_disposable_state_and_secondary_monitor(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.delenv("PALPLUS_STATE_PATH", raising=False)
    monkeypatch.delenv("PALPLUS_MONITOR", raising=False)
    monkeypatch.setattr("palworld_companion.app.main", lambda: calls.append("started"))
    assert entrypoint.run(["--fresh"]) == 0
    assert calls == ["started"]
    assert Path(entrypoint.os.environ["PALPLUS_STATE_PATH"]).parent == tmp_path
    assert entrypoint.os.environ["PALPLUS_MONITOR"] == "secondary"


def test_unknown_installed_option_fails_before_opening_the_ui():
    try:
        entrypoint.run(["--surprise"])
    except ValueError as error:
        assert "Unknown PalPlus option" in str(error)
    else:
        raise AssertionError("Unknown installed options must not be silently ignored")


def test_diagnostic_failure_writes_a_log_without_opening_a_dialog(monkeypatch):
    calls = []
    monkeypatch.setattr(entrypoint.sys, "argv", ["PalPlus.exe", "--surprise"])
    monkeypatch.setattr(entrypoint, "_report_startup_error", lambda **kwargs: calls.append(kwargs))
    assert entrypoint.main() == 1
    assert calls == [{"show_dialog": False}]
