@echo off
REM Steltic Hub India -- double-clickable entry point. Keeps no console window open once the app is up.
REM Data: %LOCALAPPDATA%\steltic_hub_india, port 8301 (the US Steltic Hub keeps %LOCALAPPDATA%\Steltic and 8300).
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0Steltic.ps1" %*
