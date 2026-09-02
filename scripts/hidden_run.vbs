' Silent process launcher for Task Scheduler actions.
' Unlike "powershell -WindowStyle Hidden", WScript.Shell.Run with window
' style 0 never allocates a visible console window at all -- no flash.
'
' Each command-line token (exe + each arg) is passed as a SEPARATE argument
' to this script; we re-quote them ourselves and hand the whole line to
' Shell.Run. Exit code of the child process is propagated.
'
' Usage (as a Scheduled Task action):
'   Program:    wscript.exe
'   Arguments:  //B //Nologo "C:\...\hidden_run.vbs" "powershell.exe" "-NoProfile" "-File" "C:\...\script.ps1"

Set objShell = CreateObject("WScript.Shell")

' cwd anchor (2026-08-11): Task Scheduler launches wscript with cwd=System32
' whenever a task omits WorkingDirectory, and children inherit it -- which
' breaks repo-relative action commands ("python trader.py ..." exits 2; the
' Aug 8-11 silent backup outage). Anchor the child cwd to the repo root
' (parent of this scripts\ folder) so every task using this launcher is
' immune to a lost/omitted WorkingDirectory registration.
Set fso = CreateObject("Scripting.FileSystemObject")
objShell.CurrentDirectory = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))

Set objArgs = WScript.Arguments

cmd = ""
For i = 0 To objArgs.Count - 1
    a = objArgs(i)
    cmd = cmd & """" & a & """ "
Next
cmd = Trim(cmd)

rc = objShell.Run(cmd, 0, True)
WScript.Quit rc
