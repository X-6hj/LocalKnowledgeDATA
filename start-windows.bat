@echo off
setlocal
set "BASE=%~dp0"
set "PYTHONW=%BASE%runtime\windows-python\pythonw.exe"
set "URL=http://127.0.0.1:8765"
if not exist "%PYTHONW%" (
  echo Portable Windows runtime is missing: %PYTHONW%
  pause
  exit /b 1
)
start "" /b "%PYTHONW%" "%BASE%run.py" --no-browser >nul 2>&1
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$u='%URL%/api/health'; $w=New-Object Net.WebClient; $w.Proxy=[Net.GlobalProxySelection]::GetEmptyWebProxy(); for($i=0; $i -lt 60; $i++){ try { $null=$w.DownloadString($u); Start-Process '%URL%'; exit 0 } catch {}; Start-Sleep -Milliseconds 250 }; exit 1"
if errorlevel 1 (
  echo Failed to start Folio Atlas. Check logs\server.log.
  pause
)
endlocal
