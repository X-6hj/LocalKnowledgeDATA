Option Explicit

Dim shell, fso, baseDir, pythonw, scriptPath, command, url, http, i, ready
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = fso.BuildPath(baseDir, "runtime\windows-python\pythonw.exe")
scriptPath = fso.BuildPath(baseDir, "run.py")
url = "http://127.0.0.1:8765"

If Not fso.FileExists(pythonw) Then
    MsgBox "Portable Windows runtime is missing:" & vbCrLf & pythonw, 16, "Folio Atlas"
    WScript.Quit 1
End If

command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & scriptPath & Chr(34) & " --no-browser"
Call shell.Run(command, 0, False)

ready = False
For i = 1 To 60
    On Error Resume Next
    Set http = CreateObject("Msxml2.ServerXMLHTTP.6.0")
    http.setProxy 1
    http.open "GET", url & "/api/health", False
    http.send
    If Err.Number = 0 Then
        If http.status = 200 Then ready = True
    End If
    Err.Clear
    On Error GoTo 0
    If ready Then Exit For
    WScript.Sleep 250
Next

If ready Then
    Call shell.Run(url, 1, False)
Else
    MsgBox "Failed to start Folio Atlas. Check logs\server.log.", 16, "Folio Atlas"
    WScript.Quit 1
End If
