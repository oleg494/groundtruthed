@echo off
chcp 65001 >nul
rem Однократно: задача планировщика - будни 9:55, скрытый старт daybot.
schtasks /create /f /tn TinvestDaybot /tr "wscript.exe \"%~dp0_run_hidden.vbs\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:55
echo Задача TinvestDaybot создана (будни 9:55, без окна).
echo Удалить: schtasks /delete /tn TinvestDaybot /f
