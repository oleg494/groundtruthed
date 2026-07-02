@echo off
chcp 65001 >nul
rem Daybot: запуск полностью в фоне (без окна) через VBS-лаунчер.
wscript.exe "%~dp0_run_hidden.vbs"
echo daybot запущен в фоне (окна нет). Лог: daybot\daybot.log
