@echo off
cd /d %~dp0
if not exist ".venv\Scripts\python.exe" (
  echo FieriA Python environment is missing. See README.md for setup.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" main.py
pause
