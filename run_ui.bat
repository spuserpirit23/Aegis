@echo off
rem Starts the Python bridge and the Godot UI.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Aegis virtual environment not found. Run install.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 pause
