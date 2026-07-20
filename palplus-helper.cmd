@echo off
setlocal EnableDelayedExpansion

set "PROJECT_ROOT=%~dp0"
set "PYTHON=%PROJECT_ROOT%.venv\Scripts\python.exe"
set "PYTHONW=%PROJECT_ROOT%.venv\Scripts\pythonw.exe"

if not exist "%PYTHON%" (
    echo PalPlus Helper could not find the project virtual environment.
    echo Expected: "%PYTHON%"
    echo Run the setup commands in README.md once, then try again.
    exit /b 1
)

if /i "%~1"=="--check" (
    "%PYTHON%" -c "from palworld_companion.bundle import load_bundle; print('PalPlus Helper ready, bundle ' + load_bundle()['bundle_version'])"
    exit /b !ERRORLEVEL!
)

if /i "%~1"=="--telemetry-check" (
    pushd "%PROJECT_ROOT%" >nul
    "%PYTHON%" -m palworld_companion.telemetry
    set "EXIT_CODE=!ERRORLEVEL!"
    popd >nul
    exit /b !EXIT_CODE!
)

if /i "%~1"=="--map-check" (
    pushd "%PROJECT_ROOT%" >nul
    "%PYTHON%" -m palworld_companion.map_asset
    set "EXIT_CODE=!ERRORLEVEL!"
    popd >nul
    exit /b !EXIT_CODE!
)

if /i "%~1"=="--map-provision" (
    pushd "%PROJECT_ROOT%" >nul
    "%PYTHON%" -m palworld_companion.map_asset --provision
    set "EXIT_CODE=!ERRORLEVEL!"
    popd >nul
    exit /b !EXIT_CODE!
)

if /i "%~1"=="--first-launch-check" (
    pushd "%PROJECT_ROOT%" >nul
    "%PYTHON%" -m palworld_companion.first_launch_check
    set "EXIT_CODE=!ERRORLEVEL!"
    popd >nul
    exit /b !EXIT_CODE!
)

if /i "%~1"=="--fresh" (
    set "PALPLUS_STATE_PATH=%TEMP%\PalPlusFresh-%RANDOM%-%RANDOM%.sqlite3"
    set "PALPLUS_MONITOR=secondary"
    echo Starting with a temporary blank state on the secondary monitor.
    shift
)

if /i "%~1"=="--foreground" (
    shift
    pushd "%PROJECT_ROOT%" >nul
    "%PYTHON%" -m palworld_companion %*
    set "EXIT_CODE=!ERRORLEVEL!"
    popd >nul
    exit /b !EXIT_CODE!
)

if not exist "%PYTHONW%" (
    echo PalPlus Helper could not find pythonw.exe in the project virtual environment.
    exit /b 1
)

start "PalPlus Helper" /D "%PROJECT_ROOT%" "%PYTHONW%" -m palworld_companion
if errorlevel 1 exit /b !ERRORLEVEL!
echo PalPlus Helper started. Delete chooses a destination; Ctrl+Alt+M shows or hides the minimap.
exit /b 0
