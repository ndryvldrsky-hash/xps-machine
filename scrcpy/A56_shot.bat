@echo off
rem Снимок экрана телефона A56 в буфер обмена и в «Изображения\A56».
rem -STA обязателен: буфер обмена Windows доступен только из однопоточного апартамента.
powershell -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0a56_shot.ps1"
