@echo off
rem saage installer (native Windows): create .venv\, install saage with the
rem web UI + dev extras, and run `saage doctor`. Idempotent - safe to re-run.
rem Needs Python >= 3.10 and Git for Windows on PATH (doctor checks both).
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo setup_windows.bat: python not found - install Python ^>= 3.10 from python.org first
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo setup_windows.bat: saage needs Python ^>= 3.10
    python -V
    exit /b 1
)

if not exist .venv (
    python -m venv .venv
    if errorlevel 1 exit /b 1
)
.venv\Scripts\python -m pip install --quiet --upgrade pip
.venv\Scripts\pip install --quiet -e ".[dev,server]"
if errorlevel 1 exit /b 1

echo.
rem warnings (no key yet) are expected here; the web UI itself is POSIX-only
.venv\Scripts\saage doctor
echo.
echo installed. next steps:
echo   .venv\Scripts\activate
echo   saage setup                              (choose a provider, paste an API key)
echo   saage run flows\story_writer\flow.yaml   (first live run)
echo   pytest -q                                (full offline test suite)
exit /b 0
