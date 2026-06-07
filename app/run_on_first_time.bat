@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ==================================================
echo  Check / install requirements (pip + check_requirements.py)
echo  Run this after pulling code or if the app fails on import.
echo ==================================================

rem 1) venv
if not exist "venv" (
    echo( [1/3] creating Python venv...
    python -m venv venv
    if errorlevel 1 (
        echo( ERROR: venv failed. Is Python installed?
        pause
        exit /b 1
    )
    echo( [1/3] venv created
) else (
    echo( [1/3] venv already exists
)

rem 2) activate
echo( [2/3] activate venv
call "venv\Scripts\activate.bat"
if errorlevel 1 (
    echo( ERROR: activate venv failed
    pause
    exit /b 1
)

rem 3) check + optional pip install
echo( [3/3] check requirements
python check_requirements.py
if errorlevel 1 (
    echo.
    echo( pip install -r requirements.txt ...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo( ERROR: pip install failed
        pause
        exit /b 1
    )
    echo( recheck requirements...
    python check_requirements.py
    if errorlevel 1 (
        echo( ERROR: still missing packages, see message above
        pause
        exit /b 1
    )
)
echo( requirements OK
echo.
pause
endlocal
