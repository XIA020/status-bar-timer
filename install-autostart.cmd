@echo off
REM Install autostart via HKCU Run key.
REM Usage: double-click or run from cmd.
setlocal EnableExtensions

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

REM Prefer venv pythonw, else system pythonw
set "PYW="
if exist "%ROOT%\.venv\Scripts\pythonw.exe" set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
if not defined PYW (
    for /f "usebackq delims=" %%P in (`where pythonw 2^>nul`) do (
        if not defined PYW set "PYW=%%P"
    )
)
if not defined PYW (
    echo [error] pythonw.exe not found. Create .venv first or install Python.
    pause
    exit /b 1
)

REM Build the Run key value. reg add /d does not need quoting if no spaces,
REM but our paths have spaces. Use \x22-escaped quotes.
set "CMDLINE=pythonw.exe \"%ROOT%\app.py\" --autostart"

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "ScreenTimeTracker" /t REG_SZ /d "\"%PYW%\" %CMDLINE%" /f >nul 2>&1
if errorlevel 1 (
    echo [error] Failed to register autostart.
    pause
    exit /b 1
)

echo.
echo [ok] Autostart registered.
echo   Run key : HKCU\Software\Microsoft\Windows\CurrentVersion\Run\ScreenTimeTracker
echo   pythonw : %PYW%
echo   script  : %ROOT%\app.py --autostart
echo.
echo To remove: run uninstall-autostart.cmd
pause
endlocal
