' iGAZE v3 Desktop Shortcut Creator (VBScript)
' This script creates a desktop shortcut with INSTANT LAUNCH (optimized)

Dim WshShell, fso, scriptDir, desktopPath, shortcutPath, pythonPath, iconPath, mainPath, shortcut

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Get paths
scriptDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
desktopPath = WshShell.SpecialFolders("Desktop")
shortcutPath = desktopPath & "\iGAZE v3.lnk"
pythonPath = scriptDir & "\venv\Scripts\pythonw.exe"
iconPath = scriptDir & "\assets\iGaze.ico"
mainPath = scriptDir & "\src\main.py"

' If venv pythonw doesn't exist, try system Python
If Not fso.FileExists(pythonPath) Then
    pythonPath = "pythonw.exe"
End If

' Create the shortcut - DIRECT PYTHON LAUNCH (fastest!)
Set shortcut = WshShell.CreateShortcut(shortcutPath)

With shortcut
    .TargetPath = pythonPath
    .Arguments = mainPath
    .WorkingDirectory = scriptDir & "\src"
    .IconLocation = iconPath
    .Description = "iGAZE v3 - Eye Interaction System"
    .WindowStyle = 1
    .Save
End With

MsgBox "✓ Desktop shortcut created - OPTIMIZED FOR SPEED!" & vbCrLf & vbCrLf & "Location: Desktop\iGAZE v3.lnk" & vbCrLf & vbCrLf & "Launches instantly with zero delay!", vbInformation, "iGAZE v3 Setup"
