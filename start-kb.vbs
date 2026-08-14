Option Explicit

Dim shell, fso, baseDir, url, command, http, i, ready
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
url = "http://127.0.0.1:8765"
ready = False

' Window style 0 keeps the WSL service console completely hidden.
command = "wsl.exe --cd " & Chr(34) & baseDir & Chr(34) & " bash -lc ""exec python3 run.py --no-browser"""
Call shell.Run(command, 0, False)

For i = 1 To 40
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
    MsgBox "Local knowledge base failed to start. Check logs\server.log.", 16, "Local Knowledge Base"
End If
