# «Элементы мельче относительно текста» для A56: меняет плотность экрана и тут же
# пересчитывает масштаб шрифта так, чтобы ВИДИМЫЙ размер текста остался прежним.
# Плотность задаёт размер иконок, отступов, кнопок, клавиатуры; текст же зависит
# от произведения плотность × font_scale — держим это произведение постоянным.
# Параметр: шаг плотности (например -40 уменьшить элементы, 40 вернуть).
param([string]$step = "-40")

$adb = Join-Path $PSScriptRoot 'adb.exe'
$inv = [Globalization.CultureInfo]::InvariantCulture
# Устройств может быть два сразу (кабель и Wi-Fi) — берём первое из списка.
$dev = (& $adb devices | Select-String '\sdevice$' | Select-Object -First 1) -replace '\s.*', ''
if (-not $dev) { Write-Output 'Телефон не найден'; Start-Sleep 3; exit 1 }

# -join обязателен: adb отдаёт массив строк, а -match по массиву не заполняет $Matches.
$out = (& $adb -s $dev shell wm density) -join ' '
$dens = 450
if ($out -match 'Override density:\s*(\d+)') { $dens = [int]$Matches[1] }
elseif ($out -match 'Physical density:\s*(\d+)') { $dens = [int]$Matches[1] }

$raw = ((& $adb -s $dev shell settings get system font_scale) -join '').Trim()
$font = 1.0
[double]::TryParse($raw, [Globalization.NumberStyles]::Float, $inv, [ref]$font) | Out-Null

$newDens = $dens + [int]$step
if ($newDens -lt 200) { $newDens = 200 }
if ($newDens -gt 560) { $newDens = 560 }

# Видимый размер текста = плотность * масштаб. Сохраняем его при новой плотности.
$newFont = [Math]::Round($dens * $font / $newDens, 2)
if ($newFont -lt 0.5) { $newFont = 0.5 }
if ($newFont -gt 1.5) { $newFont = 1.5 }

& $adb -s $dev shell wm density $newDens
& $adb -s $dev shell settings put system font_scale $($newFont.ToString($inv))
Write-Output ("Плотность: {0} -> {1}, шрифт: {2} -> {3}" -f $dens, $newDens, $font.ToString($inv), $newFont.ToString($inv))
Start-Sleep 2

# Смена плотности пересоздаёт дисплей Android, и окно scrcpy закрывается — поднимаем обратно.
# Запускаем напрямую, а не через планировщик: скрипт уже идёт в сеансе пользователя.
Start-Sleep 2
if (-not (Get-Process scrcpy -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath (Join-Path $PSScriptRoot 'A56.bat') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}
