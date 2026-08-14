@echo off
setlocal
set "PROJECT=%~dp0"
set "URL=http://127.0.0.1:8765"

start "Local Knowledge Base Server" /min wsl.exe --cd "%PROJECT%" bash -lc "exec python3 run.py --no-browser" >nul 2>&1
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$u='%URL%/api/health'; $w=New-Object Net.WebClient; $w.Proxy=[Net.GlobalProxySelection]::GetEmptyWebProxy(); for($i=0; $i -lt 40; $i++){ try { $null=$w.DownloadString($u); Start-Process '%URL%'; exit 0 } catch {}; Start-Sleep -Milliseconds 250 }; exit 1"
if errorlevel 1 (
  echo Failed to start the local knowledge base.
  echo Run this command to inspect the error:
  echo wsl.exe --cd "%PROJECT%" bash -lc "python3 run.py"
  pause
)
endlocal
