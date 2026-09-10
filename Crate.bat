@echo off
REM Launch the Crate browser. Double-click this, or pin it to the taskbar.
REM
REM Uses pythonw.exe so no console window hangs around behind the app.
REM %~dp0 is this file's own folder, so the shortcut keeps working wherever
REM the repo lives.

set "CRATE_HOME_DIR=%~dp0"
set "CRATE_PYTHONW=%CRATE_HOME_DIR%.venv\Scripts\pythonw.exe"

if not exist "%CRATE_PYTHONW%" goto :no_venv

start "" "%CRATE_PYTHONW%" -m crate ui
exit /b 0

:no_venv
echo Crate is not set up yet: no virtualenv at
echo    %CRATE_HOME_DIR%.venv
echo.
echo From %CRATE_HOME_DIR% run:
echo    uv venv
echo    uv pip install -e ".[dev]"
echo.
pause
exit /b 1
