# Картинка из буфера обмена Windows → в галерею телефона A56.
# Буферы Windows и Android независимы, и положить изображение в буфер Android через adb нельзя
# (clipboard-API для картинок из shell недоступен). Поэтому кладём файл в /sdcard/Pictures и
# сообщаем медиасканеру — снимок появляется первым в галерее, и в чате на телефоне его можно
# приложить в два тапа.
$ErrorActionPreference = 'Stop'
$adb = Join-Path $PSScriptRoot 'adb.exe'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

if (-not [Windows.Forms.Clipboard]::ContainsImage()) {
    Write-Output 'В буфере обмена нет картинки. Сначала сделай снимок (Win+Shift+S или PrintScreen).'
    Start-Sleep 3
    exit 1
}

$dev = (& $adb devices | Select-String '\sdevice$' | Select-Object -First 1) -replace '\s.*', ''
if (-not $dev) { Write-Output 'Телефон не найден'; Start-Sleep 3; exit 1 }

$img = [Windows.Forms.Clipboard]::GetImage()
$local = Join-Path $env:TEMP ('win_clip_' + (Get-Date -Format 'yyyy-MM-dd_HH-mm-ss') + '.png')
$img.Save($local, [Drawing.Imaging.ImageFormat]::Png)
$img.Dispose()

$name = Split-Path $local -Leaf
$remote = "/sdcard/Pictures/FromWindows/$name"
& $adb -s $dev shell "mkdir -p /sdcard/Pictures/FromWindows" | Out-Null
& $adb -s $dev push $local $remote | Out-Null
# Без этого файл лежит на диске, но галерея его не видит
& $adb -s $dev shell "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://$remote" | Out-Null

# Держим на телефоне последние 30 снимков, чтобы папка не разрасталась
& $adb -s $dev shell "ls -t /sdcard/Pictures/FromWindows/*.png 2>/dev/null | tail -n +31 | xargs -r rm -f" | Out-Null
Remove-Item $local -Force

Write-Output ("Отправлено на телефон: $name — открой галерею, снимок первый.")
Start-Sleep 2
