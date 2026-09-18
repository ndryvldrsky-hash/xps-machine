@echo off
chcp 65001 >nul
rem Возврат телефона A56 к обычному автоповороту по акселерометру.
set S=%~dp0
"%S%adb.exe" shell settings put system user_rotation 0
"%S%adb.exe" shell settings put system accelerometer_rotation 1
echo Автоповорот возвращён.
timeout /t 2 >nul
