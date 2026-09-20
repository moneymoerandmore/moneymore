Option Explicit

Dim shell, fso, scriptDir, serviceScript, action, command, exitCode
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
serviceScript = fso.BuildPath(scriptDir, "local_service.ps1")
action = "Run"
If WScript.Arguments.Count > 0 Then action = WScript.Arguments(0)

command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ _
    & serviceScript & """ -Action " & action

' Window style 0 prevents Windows from creating a visible console window.
' Waiting keeps Task Scheduler's task state aligned with the invoked process.
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
