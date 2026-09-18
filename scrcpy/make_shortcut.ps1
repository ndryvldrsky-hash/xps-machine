# Ярлык на рабочем столе для запуска экрана телефона A56 (scrcpy).
# Отдельным файлом, а не строкой через WinRM: кириллица в аргументах приезжает двойным UTF-8.
# WScript.Shell не умеет сохранять ярлык с кириллицей в имени (не-Unicode системная локаль),
# поэтому создаём ASCII-именем и переименовываем через .NET.
$ErrorActionPreference = 'Stop'
$s = 'W:\tools\scrcpy-win64-v3.1'
$desktop = [Environment]::GetFolderPath('Desktop')
$tmp = Join-Path $desktop 'A56_tmp.lnk'
$final = Join-Path $desktop 'Экран A56.lnk'
$w = New-Object -ComObject WScript.Shell
$lnk = $w.CreateShortcut($tmp)
$lnk.TargetPath = Join-Path $s 'A56.bat'
$lnk.WorkingDirectory = $s
$lnk.IconLocation = (Join-Path $s 'scrcpy.exe') + ',0'
$lnk.Description = 'Ekran telefona A56 (scrcpy)'
$lnk.Save()
if (Test-Path $final) { Remove-Item $final -Force }
[IO.File]::Move($tmp, $final)
Write-Output ('создан: ' + (Test-Path $final))
