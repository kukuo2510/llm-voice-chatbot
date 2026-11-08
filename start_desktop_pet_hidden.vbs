' Start the desktop pet (TTS server + stage + pet) without console windows.
' Logs: logs\tts.log and logs\pet.log
Set shell = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run """" & here & "\scripts\launch_hidden.bat""", 0, False
