# Окно «Консоль AirTies» на XPS: журнал UART вживую (06.11.2026). Открывается задачей под rdpuser.
$Host.UI.RawUI.WindowTitle = 'Консоль AirTies (UART, COM17)'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
# Окно открывается СРАЗУ на SHARP: запуск — `wt.exe -w new --pos 2560,374 --maximized … -File этот.ps1`
# (задача AlenaAirtiesOkno); переносить окно после открытия нельзя (хозяин 06.11).
Get-Content 'W:\Tools\airties_uart\консоль.log' -Encoding UTF8 -Tail 60 -Wait
