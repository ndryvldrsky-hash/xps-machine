@echo off
chcp 65001 >nul
rem Экран телефона A56 (SM-A566B) на XPS через scrcpy.
rem Сначала USB (-d), если кабеля нет — сеть (-e). Вывод пишем в лог рядом со скриптом:
rem задача планировщика выполняется в чужой сессии, и без лога причину падения не увидеть.
set S=%~dp0
set LOG=%S%A56.log
echo ==== %DATE% %TIME% ==== >> "%LOG%"
"%S%adb.exe" start-server >> "%LOG%" 2>&1
"%S%adb.exe" devices -l >> "%LOG%" 2>&1
"%S%scrcpy.exe" -d --window-title=A56 --stay-awake %* >> "%LOG%" 2>&1
if not errorlevel 1 exit /b 0
echo -- USB не сработал, пробую сеть >> "%LOG%"
"%S%adb.exe" connect 100.89.226.62:5555 >> "%LOG%" 2>&1
"%S%scrcpy.exe" -e --window-title=A56 --stay-awake %* >> "%LOG%" 2>&1
if not errorlevel 1 exit /b 0
echo -- не удалось >> "%LOG%"
