@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing project .venv. Please follow README setup instructions.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 "scripts\launch.py" %*
set "CF_STITCH_EXIT=%ERRORLEVEL%"
if not "%CF_STITCH_EXIT%"=="0" (
  echo CF-Stitch exited with code %CF_STITCH_EXIT%.
  pause
)
exit /b %CF_STITCH_EXIT%
