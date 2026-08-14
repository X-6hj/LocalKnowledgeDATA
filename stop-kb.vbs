Option Explicit

Dim shell, fso, baseDir, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
command = "wsl.exe --cd " & Chr(34) & baseDir & Chr(34) & " bash -lc ""python3 stop.py"""
Call shell.Run(command, 0, True)
