# Порядок на рабочем столе XPS: один ярлык подключения к телефону — на столе той учётки,
# которая реально сидит за консолью (rdpuser), а десяток служебных ярлыков размеров и
# ориентации — в подпапку, чтобы не засоряли экран.
$ErrorActionPreference = 'Stop'
$s = 'W:\tools\scrcpy-win64-v3.1'
$pub = Join-Path $env:PUBLIC 'Desktop'
$w = New-Object -ComObject WScript.Shell

# 1) папка для служебных ярлыков
$folder = Join-Path $pub 'A56 настройки'
New-Item -ItemType Directory -Force -Path $folder | Out-Null

# 2) переносим всё, кроме главного «Экран A56»
foreach ($f in Get-ChildItem $pub -Filter 'A56 *.lnk') {
    Move-Item $f.FullName (Join-Path $folder $f.Name) -Force
}

# 3) главный ярлык — на рабочий стол консольного пользователя. WScript.Shell не сохраняет
# кириллицу в имени файла (не-Unicode системная локаль), поэтому создаём ASCII-именем
# и переименовываем через .NET.
$who = (quser 2>$null | Select-String 'console') -replace '^\s*>?\s*(\S+)\s+console.*', '$1'
$desk = "W:\Users\$($who.Trim())\Desktop"
if (-not (Test-Path $desk)) { $desk = $pub }
$tmp = Join-Path $desk 'A56_tmp.lnk'
$final = Join-Path $desk 'Экран A56.lnk'
$lnk = $w.CreateShortcut($tmp)
$lnk.TargetPath = Join-Path $s 'A56.bat'
$lnk.WorkingDirectory = $s
$lnk.IconLocation = (Join-Path $s 'scrcpy.exe') + ',0'
$lnk.Description = 'Ekran telefona A56 (scrcpy)'
$lnk.WindowStyle = 7
$lnk.Save()
if (Test-Path $final) { Remove-Item $final -Force }
[IO.File]::Move($tmp, $final)

Write-Output ("главный ярлык: " + $final + " -> " + (Test-Path $final))
Write-Output ("в папке настроек: " + (Get-ChildItem $folder -Filter '*.lnk').Count + " шт.")
