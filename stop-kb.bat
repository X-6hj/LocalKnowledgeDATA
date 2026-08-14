@echo off
setlocal
set "PROJECT=%~dp0"
wsl.exe --cd "%PROJECT%" bash -lc "python3 stop.py"
timeout /t 2 >nul
endlocal
