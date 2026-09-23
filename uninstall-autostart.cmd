@echo off
REM Remove autostart Run key.
setlocal

reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "ScreenTimeTracker" >nul 2>&1
if errorlevel 1 (
    echo [ok] Autostart not registered. Nothing to do.
    pause
    exit /b 0
)

reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "ScreenTimeTracker" /f
if errorlevel 1 (
    echo [error] Failed to remove.
    pause
    exit /b 1
)

echo [ok] Autostart removed.
pause
endlocal
