#!/usr/bin/env python3
"""使用 Windows Shell 原生“打开方式”对话框。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if os.name != "nt" or len(sys.argv) != 2:
        return 2

    from ctypes import Structure, byref, c_long, c_uint, c_void_p, c_wchar_p, windll

    class OpenAsInfo(Structure):
        _fields_ = [
            ("pcszFile", c_wchar_p),
            ("pcszClass", c_wchar_p),
            ("oaifInFlags", c_uint),
        ]

    activator = Path(__file__).with_name("activate_open_with.ps1")
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(activator),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    target = str(Path(sys.argv[1]).resolve())
    info = OpenAsInfo(target, None, 0x00000001 | 0x00000004)
    dialog = windll.shell32.SHOpenWithDialog
    dialog.argtypes = [c_void_p, c_void_p]
    dialog.restype = c_long
    return int(dialog(None, byref(info)))


if __name__ == "__main__":
    raise SystemExit(main())
