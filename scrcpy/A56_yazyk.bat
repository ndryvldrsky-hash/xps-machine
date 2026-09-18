@echo off
rem Запасное переключение языка на телефоне: штатный keyevent Android.
rem Основной способ — CapsLock, который шлёт в окно Ctrl+Space (см. a56_hotkeys.ahk).
"%~dp0adb.exe" shell input keyevent KEYCODE_LANGUAGE_SWITCH
