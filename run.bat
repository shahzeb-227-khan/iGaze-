@echo off
REM iGAZE v3 - Quick Launch from Root
setlocal enabledelayedexpansion
cd /d "%~dp0"
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" src/main.py
) else (
    start "" pythonw.exe src/main.py
)
exit /b 0
