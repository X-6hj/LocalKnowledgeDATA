Option Explicit

Dim shell, fso, baseDir, pythonExe, stopScript, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonExe = fso.BuildPath(baseDir, "runtime\windows-python\python.exe")
stopScript = fso.BuildPath(baseDir, "stop.py")

If Not fso.FileExists(pythonExe) Then
    MsgBox "Portable Windows runtime is missing:" & vbCrLf & pythonExe, 16, "Folio Atlas"
    WScript.Quit 1
End If

command = Chr(34) & pythonExe & Chr(34) & " " & Chr(34) & stopScript & Chr(34)
Call shell.Run(command, 0, True)
