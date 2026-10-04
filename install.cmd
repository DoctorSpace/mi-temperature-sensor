@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 (
        echo Could not create environment. Install Python first.
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Installation failed. Check the message above.
    pause
    exit /b 1
)
echo Ready. Launch start_widget.cmd
pause
