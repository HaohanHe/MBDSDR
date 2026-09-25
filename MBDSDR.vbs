' MBDSDR 双击入口（Windows，无控制台、无黑框闪烁）
' 隐藏调用 scripts\launch_mbdsdr.bat，由其探测 pythonw 并启动桌面端。
Option Explicit
Dim fso, sh, d
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
d = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = d
sh.Run """" & d & "\scripts\launch_mbdsdr.bat""", 0, False
