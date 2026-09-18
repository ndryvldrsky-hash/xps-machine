@echo off
rem Полный сброс: заводская плотность, шрифт 1.0, автоповорот. Ctrl+0 в окне телефона.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0a56_density.ps1" reset
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0a56_font.ps1" reset
call "%~dp0A56_avtopovorot.bat"
