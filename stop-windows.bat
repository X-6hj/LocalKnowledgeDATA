@echo off
setlocal
set "BASE=%~dp0"
set "PYTHON=%BASE%runtime\windows-python\python.exe"
if not exist "%PYTHON%" (
  echo Portable Windows runtime is missing: %PYTHON%
  pause
  exit /b 1
)
"%PYTHON%" "%BASE%stop.py"
endlocal
