# Масштаб шрифта телефона A56 отдельно от плотности экрана: меняет только размер текста,
# иконки и отступы остаются прежними (за них отвечает a56_density.ps1).
# Параметр: шаг (например -0.05 или 0.05), либо слово reset — вернуть 1.0.
param([string]$step = "-0.05")

$adb = Join-Path $PSScriptRoot 'adb.exe'
# Устройств может быть два сразу (кабель и Wi-Fi) — берём первое из списка.
$dev = (& $adb devices | Select-String '\sdevice$' | Select-Object -First 1) -replace '\s.*', ''
if (-not $dev) { Write-Output 'Телефон не найден'; Start-Sleep 3; exit 1 }

if ($step -eq 'reset') {
    & $adb -s $dev shell settings put system font_scale 1.0
    Write-Output 'Масштаб шрифта возвращён к 1.0'
} else {
    # Culture у adb и у PowerShell разная — разбираем и собираем число только через InvariantCulture,
    # иначе на локали с запятой в дробях Android получит «0,8» и молча проигнорирует.
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $raw = ((& $adb -s $dev shell settings get system font_scale) -join '').Trim()
    $cur = 1.0
    [double]::TryParse($raw, [Globalization.NumberStyles]::Float, $inv, [ref]$cur) | Out-Null
    $new = [Math]::Round($cur + [double]::Parse($step, $inv), 2)
    # Ниже 0.5 текст нечитаем, выше 1.5 смысла нет
    if ($new -lt 0.5) { $new = 0.5 }
    if ($new -gt 1.5) { $new = 1.5 }
    $s = $new.ToString($inv)
    & $adb -s $dev shell settings put system font_scale $s
    Write-Output ("Шрифт: {0} -> {1}" -f $cur.ToString($inv), $s)
}
Start-Sleep 2
