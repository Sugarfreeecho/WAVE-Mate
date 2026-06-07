@echo off
setlocal
chcp 65001 >nul
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "PYTHONIOENCODING=utf-8"
set "PYTHON_EXE=%ROOT%python\python.exe"
set "LAUNCHER_PY=%ROOT%app\tray_launcher.py"
if not exist "%PYTHON_EXE%" (
  echo Missing: "%PYTHON_EXE%"
  pause
  exit /b 1
)
if not exist "%LAUNCHER_PY%" (
  echo Missing: "%LAUNCHER_PY%"
  pause
  exit /b 1
)
"%PYTHON_EXE%" "%LAUNCHER_PY%"
if errorlevel 1 pause
endlocal
