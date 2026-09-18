# Снимок экрана телефона A56: кладём PNG в «Изображения\A56» и сразу помещаем в буфер
# обмена Windows — чтобы вставлять в переписку по Ctrl+V, не открывая папку.
$ErrorActionPreference = 'Stop'
$adb = Join-Path $PSScriptRoot 'adb.exe'
$dev = (& $adb devices | Select-String '\sdevice$' | Select-Object -First 1) -replace '\s.*', ''
if (-not $dev) { Write-Output 'Телефон не найден'; Start-Sleep 3; exit 1 }

$dir = Join-Path ([Environment]::GetFolderPath('MyPictures')) 'A56'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$file = Join-Path $dir ('a56_' + (Get-Date -Format 'yyyy-MM-dd_HH-mm-ss') + '.png')

# exec-out отдаёт PNG в поток без подмены переводов строк — через обычный shell картинка бьётся
$proc = Start-Process -FilePath $adb -ArgumentList @('-s', $dev, 'exec-out', 'screencap', '-p') `
    -NoNewWindow -Wait -RedirectStandardOutput $file -PassThru
if ($proc.ExitCode -ne 0 -or -not (Test-Path $file)) { Write-Output 'Снимок не получился'; Start-Sleep 3; exit 1 }

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$img = [Drawing.Image]::FromFile($file)
$bmp = New-Object Drawing.Bitmap $img
$img.Dispose()
# SetImage кладёт в буфер данные, принадлежащие ЭТОМУ процессу: как только скрипт завершается,
# вставлять уже нечего. SetDataObject со вторым аргументом $true просит Windows скопировать
# данные себе, и снимок переживает выход процесса.
$data = New-Object Windows.Forms.DataObject
$data.SetImage($bmp)
[Windows.Forms.Clipboard]::SetDataObject($data, $true)
$bmp.Dispose()
Write-Output ('Снимок в буфере обмена и в файле: ' + $file)
Start-Sleep 1
