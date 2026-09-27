@echo off
rem Kuji v2.2 dev console launcher (double-click to run)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m client.ui.debug_console
if errorlevel 1 (
    echo.
    echo [Failed] Please send the error message above to the developer.
    pause
)
