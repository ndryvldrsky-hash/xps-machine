# Ступенчатое изменение плотности экрана телефона A56 (DPI): чем меньше, тем мельче
# интерфейс и тем больше влезает на экран. Физическая плотность A56 — 450.
# Параметр: шаг (например -40 или 40), либо слово reset — вернуть заводскую.
param([string]$step = "-40")

$adb = Join-Path $PSScriptRoot 'adb.exe'
# Устройств может быть два сразу (кабель и Wi-Fi) — берём первое из списка.
$dev = (& $adb devices | Select-String '\sdevice$' | Select-Object -First 1) -replace '\s.*', ''
if (-not $dev) { Write-Output 'Телефон не найден'; Start-Sleep 3; exit 1 }

if ($step -eq 'reset') {
    & $adb -s $dev shell wm density reset
    Write-Output 'Плотность возвращена к заводской (450)'
} else {
    # -join обязателен: adb отдаёт массив строк, а -match по массиву фильтрует его
    # и НЕ заполняет $Matches — без склейки в строку разбор молча ломается.
    $out = (& $adb -s $dev shell wm density) -join ' '
    # «Physical density: 450  Override density: 340» — берём переопределённую, если есть
    $cur = 0
    if ($out -match 'Override density:\s*(\d+)') { $cur = [int]$Matches[1] }
    elseif ($out -match 'Physical density:\s*(\d+)') { $cur = [int]$Matches[1] }
    $new = $cur + [int]$step
    # Ниже 200 интерфейс становится нечитаемым, выше 560 — бессмысленно крупным
    if ($new -lt 200) { $new = 200 }
    if ($new -gt 560) { $new = 560 }
    & $adb -s $dev shell wm density $new
    Write-Output ("Плотность: {0} -> {1}" -f $cur, $new)
}
Start-Sleep 2

# Смена плотности пересоздаёт дисплей Android, и окно scrcpy закрывается — поднимаем обратно.
# Запускаем напрямую, а не через планировщик: скрипт уже идёт в сеансе пользователя.
Start-Sleep 2
if (-not (Get-Process scrcpy -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath (Join-Path $PSScriptRoot 'A56.bat') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}
