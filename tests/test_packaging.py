from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_installer_is_per_user_unsigned_and_preserves_personal_data():
    script = (ROOT / "packaging" / "PalPlus.iss").read_text(encoding="utf-8")
    assert '#define MyAppVersion "1.0.1"' in script
    assert "https://github.com/shin86dev/palworld-companion" in script
    assert "github.com/OWNER" not in script
    assert "PrivilegesRequired=lowest" in script
    assert r"DefaultDirName={localappdata}\Programs\PalPlus" in script
    assert "[UninstallDelete]" not in script
    assert "SignTool=" not in script
    assert "SetupIconFile=" in script


def test_frozen_build_contains_release_assets_and_no_palradar():
    spec = (ROOT / "packaging" / "PalPlus.spec").read_text(encoding="utf-8")
    release_sources = "\n".join(
        path.relative_to(ROOT).as_posix()
        for base in (ROOT / "src", ROOT / "packaging")
        for path in base.rglob("*")
        if path.is_file()
    ).casefold()
    assert '"pyuepak.oodle"' in spec
    assert 'root / "src" / "palworld_companion" / "data"' in spec
    assert 'root / "src" / "palworld_companion" / "assets"' in spec
    assert "palplus.ico" in spec
    assert "assets/*.json" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "pal_radar.py" not in release_sources
    assert "pal-radar.json" not in release_sources


def test_workflow_is_a_single_minimal_release_job():
    workflow = (ROOT / ".github" / "workflows" / "windows-package.yml").read_text(encoding="utf-8")
    assert workflow.count("runs-on:") == 1
    assert "python -m pytest -q" in workflow
    assert 'Start-Process ".\\dist\\PalPlus\\PalPlus.exe"' in workflow
    assert '-ArgumentList "--check" -Wait -PassThru' in workflow
    assert "$process.ExitCode" in workflow
    assert "startup-error.log" in workflow
    assert "PalPlus-Setup.exe.sha256" in workflow
    assert 'tags: ["v*"]' in workflow
    assert "github.ref_type == 'tag'" in workflow
    assert "matrix:" not in workflow
    assert "coverage" not in workflow.casefold()


def test_release_metadata_has_one_version():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package = (ROOT / "src" / "palworld_companion" / "__init__.py").read_text(encoding="utf-8")
    assert 'version = "1.0.1"' in pyproject
    assert '__version__ = "1.0.1"' in package


def test_public_readme_links_the_latest_installer_and_describes_privacy_precisely():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://github.com/shin86dev/palworld-companion/releases/latest/download/PalPlus-Setup.exe" in readme
    assert "The app does not upload that cache or your gameplay data." in readme
    assert "Map imagery is prepared privately from your own Palworld installation and is never uploaded." not in readme
    assert "Railway-hosted support database" in readme
    assert "No automatic cloud uploads or background telemetry" in readme
