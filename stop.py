#!/usr/bin/env python3
"""安全停止由本项目启动的知识库进程。"""
from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PID_FILE = BASE_DIR / ".server.pid"


def _windows_project_match(process_ref: str) -> tuple[str, str]:
    """生成严格的项目进程匹配条件，避免命中同名前缀目录。"""
    script = str(BASE_DIR / "run.py").replace("'", "''")
    runtime = (str(BASE_DIR / "runtime" / "windows-python").rstrip("\\/") + "\\").replace("'", "''")
    preamble = (
        f"$scriptPattern = [regex]::Escape('{script}') + '(?:\"|\\s|$)'; "
        f"$runtimePrefix = '{runtime}'; "
    )
    relative_script = r"(?i)(?:^|\s|\")run\.py(?:\"|\s|$)"
    condition = (
        f"(({process_ref}.CommandLine -match $scriptPattern) -or "
        f"(({process_ref}.CommandLine -match '{relative_script}') -and "
        f"({process_ref}.ExecutablePath -like ($runtimePrefix + '*'))))"
    )
    return preamble, condition


def stop_windows_process(pid: int) -> int:
    """核对 Windows 进程命令行后停止，避免 PID 复用误杀。"""
    preamble, belongs_to_project = _windows_project_match("$p")
    script = (
        preamble
        + f'$p = Get-CimInstance Win32_Process -Filter "ProcessId = {pid}"; '
        "if ($null -eq $p) { exit 2 }; "
        f"if (-not {belongs_to_project}) {{ exit 3 }}; "
        f"Stop-Process -Id {pid} -Force; exit 0"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False
    )
    if result.returncode == 3:
        print(f"Refusing to stop PID {pid}: it is not this knowledge base process.")
        return 1
    if result.returncode not in {0, 2}:
        print(f"Failed to stop PID {pid} (PowerShell code {result.returncode}).")
        return 1
    PID_FILE.unlink(missing_ok=True)
    print("Knowledge base stopped." if result.returncode == 0 else "Knowledge base process is already stopped.")
    return 0


def stop_windows_project_processes() -> int:
    """PID 文件丢失时，仅按精确脚本或项目专用运行时恢复停止。"""
    preamble, belongs_to_project = _windows_project_match("$_")
    script = (
        preamble
        + "$matches = @(Get-CimInstance Win32_Process | Where-Object { "
        f"{belongs_to_project} "
        "}); "
        "if ($matches.Count -eq 0) { exit 2 }; "
        "$matches | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; exit 0"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False
    )
    if result.returncode not in {0, 2}:
        print(f"Failed to find or stop this knowledge base process (PowerShell code {result.returncode}).")
        return 1
    print("Knowledge base stopped." if result.returncode == 0 else "Knowledge base is not running.")
    return 0


def main() -> int:
    if not PID_FILE.exists():
        if os.name == "nt":
            return stop_windows_project_processes()
        print("Knowledge base is not running (PID file not found).")
        return 0
    try:
        data = json.loads(PID_FILE.read_text(encoding="utf-8"))
        pid = int(data["pid"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        PID_FILE.unlink(missing_ok=True)
        print("Removed an invalid PID file.")
        return 0

    if os.name == "nt":
        return stop_windows_process(pid)

    # Linux /proc 提供真实命令行，先确认 PID 属于本项目，避免误杀复用 PID 的其他进程。
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        PID_FILE.unlink(missing_ok=True)
        print("Knowledge base process is already stopped.")
        return 0
    if "run.py" not in cmdline:
        print(f"Refusing to stop PID {pid}: it is not the knowledge base process.")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Knowledge base stopped (PID {pid}).")
    except ProcessLookupError:
        print("Knowledge base process is already stopped.")
    PID_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
