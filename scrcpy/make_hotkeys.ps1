# Горячие клавиши для телефона A56. Поле Hotkey у ярлыка Windows работает глобально, но
# только если ярлык лежит на рабочем столе или в меню «Пуск» — кладём в «Пуск», чтобы не
# засорять стол. Сочетания вида Ctrl+Alt+<клавиша>; система сама их регистрирует.
# Имена с кириллицей WScript.Shell не сохраняет (не-Unicode системная локаль) — создаём
# ASCII-именем и переименовываем через .NET.
$ErrorActionPreference = 'Stop'
$s = 'W:\tools\scrcpy-win64-v3.1'
# WinRM работает под alena, а за консолью сидит rdpuser — меню «Пуск» нужно ЕГО,
# иначе горячие клавиши не зарегистрируются в его сеансе.
$who = (quser 2>$null | Select-String 'console') -replace '^\s*>?\s*(\S+)\s+console.*', '$1'
$who = $who.Trim()
if (-not $who) { $who = $env:USERNAME }
$start = "W:\Users\$who\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\A56"
New-Item -ItemType Directory -Force -Path $start | Out-Null
$w = New-Object -ComObject WScript.Shell

$items = @(
    @{ bat = 'A56_shot.bat';            name = 'A56 снимок экрана.lnk';    key = 'Ctrl+Alt+S'; icon = '109' },
    @{ bat = 'A56_shrift_menshe.bat';   name = 'A56 шрифт меньше.lnk';     key = 'Ctrl+Alt+J'; icon = '70'  },
    @{ bat = 'A56_shrift_bolshe.bat';   name = 'A56 шрифт больше.lnk';     key = 'Ctrl+Alt+K'; icon = '71'  },
    @{ bat = 'A56_elementy_melche.bat'; name = 'A56 элементы мельче.lnk';  key = 'Ctrl+Alt+N'; icon = '54'  },
    @{ bat = 'A56_elementy_krupnee.bat';name = 'A56 элементы крупнее.lnk'; key = 'Ctrl+Alt+M'; icon = '55'  },
    @{ bat = 'A56.bat';                 name = 'A56 экран.lnk';            key = 'Ctrl+Alt+A'; icon = ''    }
)

foreach ($i in $items) {
    $tmp = Join-Path $start ('tmp_' + $i.bat + '.lnk')
    $final = Join-Path $start $i.name
    $lnk = $w.CreateShortcut($tmp)
    $lnk.TargetPath = Join-Path $s $i.bat
    $lnk.WorkingDirectory = $s
    if ($i.icon) { $lnk.IconLocation = 'C:\Windows\System32\imageres.dll,' + $i.icon }
    else { $lnk.IconLocation = (Join-Path $s 'scrcpy.exe') + ',0' }
    $lnk.WindowStyle = 7   # свёрнутое окно: bat отрабатывает молча
    $lnk.Hotkey = $i.key
    $lnk.Save()
    if (Test-Path $final) { Remove-Item $final -Force }
    [IO.File]::Move($tmp, $final)
    Write-Output ($i.key + '  ->  ' + $i.name)
}
Write-Output ('папка: ' + $start)
