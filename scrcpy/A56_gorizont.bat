@echo off
chcp 65001 >nul
rem Принудительный альбомный режим на телефоне A56: выключаем автоповорот и фиксируем 90 градусов.
rem Это поворот самого Android, а не окна — приложения перестраивают вёрстку под альбом.
set S=%~dp0
"%S%adb.exe" shell settings put system accelerometer_rotation 0
"%S%adb.exe" shell settings put system user_rotation 1
echo Телефон переведён в горизонтальный режим.
timeout /t 2 >nul
