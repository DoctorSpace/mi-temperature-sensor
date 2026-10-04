@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "from PySide6 import QtCore, QtGui, QtWidgets; import bleak"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm MiTemperatureWidget.spec
if errorlevel 1 goto failed
echo Ready: %CD%\dist\MiTemperatureWidget.exe
pause
exit /b 0

:failed
echo Build failed. Check the error above. Do not publish the existing EXE.
pause
exit /b 1
