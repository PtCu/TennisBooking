@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "src\__main__.py"
) else (
    python "src\__main__.py"
)
if errorlevel 1 pause
