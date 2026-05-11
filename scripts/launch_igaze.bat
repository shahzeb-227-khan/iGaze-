@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0\.."
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" src/main.py
) else (
    start "" pythonw.exe src/main.py
)
exit /b 0
