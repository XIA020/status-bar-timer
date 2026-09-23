@echo off
REM One-click start: launches the tracker (tray icon) in background.
setlocal

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "PYW="
if exist "%ROOT%\.venv\Scripts\pythonw.exe" set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
if not defined PYW (
    for /f "usebackq delims=" %%P in (`where pythonw 2^>nul`) do (
        if not defined PYW set "PYW=%%P"
    )
)
if not defined PYW (
    echo [error] pythonw.exe not found.
    pause
    exit /b 1
)

if not exist "%ROOT%\data" mkdir "%ROOT%\data" >nul 2>&1

start "ScreenTimeTracker" /min "%PYW%" "%ROOT%\app.py"

echo.
echo Screen Time Tracker started (in tray, minimized window).
echo Right-click the blue icon in system tray for menu.
echo   - Open Dashboard
echo   - Pause / Resume
echo   - Quit
echo.
echo To install autostart: install-autostart.cmd
pause
endlocal
