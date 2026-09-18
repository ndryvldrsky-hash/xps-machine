@echo off
rem Картинка из буфера обмена Windows → в галерею телефона A56 (Ctrl+Alt+V).
rem -STA обязателен: буфер обмена доступен только из однопоточного апартамента.
powershell -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0a56_send_clip.ps1"
