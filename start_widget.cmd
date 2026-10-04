@echo off
cd /d "%~dp0"
set "PYTHON=python"
set "PYTHONW=pythonw"
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON=%~dp0.venv\Scripts\python.exe"
    set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
)
"%PYTHON%" -c "import bleak, PySide6" >nul 2>&1
if errorlevel 1 (
    echo Install dependencies first:
    echo Run install.cmd from this folder.
    pause
    exit /b 1
)
start "" "%PYTHONW%" "%~dp0widget.pyw"
