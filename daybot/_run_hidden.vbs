Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
proj = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
pyw = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe"
sh.CurrentDirectory = proj
sh.Run """" & pyw & """ -m daybot.run", 0, False
