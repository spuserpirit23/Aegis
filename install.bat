@echo off
setlocal
cd /d "%~dp0"

rem Creates .venv (the environment run.bat / run_ui.bat expect) and installs
rem dependencies into it. Pass --local to also install the optional on-device
rem emotion classifier (torch + transformers, several GB).

set "PY="
where py >nul 2>nul && py -3.11 -c "import sys" >nul 2>nul && set "PY=py -3.11"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3.11 was not found. Install it from https://www.python.org/downloads/ and re-run.
    goto :fail
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment with %PY% ...
    %PY% -m venv .venv || goto :fail
)

".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

if /i "%~1"=="--local" (
    ".venv\Scripts\python.exe" -m pip install -r requirements-local.txt || goto :fail
)

if not exist ".env" (
    echo.
    echo No .env file found. Create one containing:
    echo     GEMINI_API_KEY=your-key-here
    echo Without it Aegis runs in fallback mode.
)

echo.
echo Install complete. Start the UI with run_ui.bat, or voice/CLI mode with run.bat.
endlocal
exit /b 0

:fail
echo.
echo Installation failed.
pause
endlocal
exit /b 1
