@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

set "PY_BIN=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY_BIN%" set "PY_BIN=python"

if not "%~1"=="--inside" if not defined WT_SESSION (
    where wt.exe >nul 2>nul
    if not errorlevel 1 (
        start "" wt.exe -d "%ROOT%" cmd /c call "%~f0" --inside
        exit /b 0
    )
)

"%PY_BIN%" "%ROOT%\src\__main__.py"
if errorlevel 1 pause
