[Console]::OutputEncoding=[Text.Encoding]::UTF8
# Яндекс на XPS — всегда с портом отладки 9229 (17.11.2026, хозяин: «надо убрать способ запуска Яндекса без порта,
# чтобы всегда открывался с портом»). Порт Яндекс даёт только на нестандартном каталоге профиля, поэтому все пути
# запуска ведут через ссылку-junction «User Data CDP» (тот же профиль). Смешанные запуски «User Data» и «User Data CDP»
# — для браузера два разных каталога одного профиля: второй упирается в занятый профиль (зависание 17.11 после перезагрузки).
# Сторож: задача AlenaYandexPort (SYSTEM, при входе и раз в день) — Яндекс сам переписывает автозапуск и команды открытия.
# Повторный прогон ничего не меняет, если всё уже с портом. Копии «до» — W:\ProgramData\alena\zapasnik\yandex_port_20261117.
$exe   = 'W:\Program Files\Yandex\YandexBrowser\Application\browser.exe'
$flags = '"--user-data-dir=W:\Users\rdpuser\AppData\Local\Yandex\YandexBrowser\User Data CDP" --remote-debugging-port=9229'
$bak   = 'W:\ProgramData\alena\zapasnik\yandex_port_20261117'
New-Item -ItemType Directory -Force $bak | Out-Null
$sid = (Get-CimInstance Win32_UserAccount -Filter "name='rdpuser'").SID
# копии «до» — только первый раз, иначе сторож затрёт их уже исправленным состоянием
if (-not (Test-Path "$bak\hkcu_run.reg")) { reg export "HKU\$sid\Software\Microsoft\Windows\CurrentVersion\Run" "$bak\hkcu_run.reg" /y | Out-Null }
if (-not (Test-Path "$bak\hklm_classes_yandex_полный.reg")) { reg export "HKLM\SOFTWARE\Classes" "$bak\hklm_classes_yandex_полный.reg" /y | Out-Null }
if (-not (Test-Path "Registry::HKEY_USERS\$sid")) { "rdpuser не вошёл — раздел HKU не загружен, проверю HKLM и ярлыки" }
# 1) автозапуск при входе
$run = "Registry::HKEY_USERS\$sid\Software\Microsoft\Windows\CurrentVersion\Run"
(Get-ItemProperty $run -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object { "$($_.Value)" -match 'YandexBrowser\\Application\\browser.exe' -and "$($_.Value)" -notmatch '9229' } | ForEach-Object {
    $new = "$($_.Value)".Replace("`"$exe`"", "`"$exe`" $flags")
    Set-ItemProperty $run -Name $_.Name -Value $new
    "автозапуск $($_.Name): $new"
}
# 2) общесистемные команды открытия (HKLM)
$n = 0
Get-ChildItem HKLM:\SOFTWARE\Classes | Where-Object { $_.PSChildName -like 'Yandex*' } | ForEach-Object {
    $k = "$($_.PSPath)\shell\open\command"
    $v = (Get-ItemProperty $k -ErrorAction SilentlyContinue).'(default)'
    if ($v -and $v -notmatch '9229' -and $v.Contains("`"$exe`"")) {
        Set-ItemProperty $k -Name '(default)' -Value $v.Replace("`"$exe`" ", "`"$exe`" $flags "); $n++
    }
}
"HKLM команд изменено: $n"
# 3) ярлыки в местах запуска (рабочие столы, меню Пуск, панель задач)
$sh = New-Object -ComObject WScript.Shell
$места = 'W:\Users\rdpuser\Desktop','W:\Users\Public\Desktop','W:\ProgramData\Microsoft\Windows\Start Menu',
         'W:\Users\rdpuser\AppData\Roaming\Microsoft\Windows\Start Menu',
         'W:\Users\rdpuser\AppData\Roaming\Microsoft\Internet Explorer\Quick Launch'
Get-ChildItem $места -Recurse -Filter *.lnk -Force -ErrorAction SilentlyContinue | ForEach-Object {
    $s = $sh.CreateShortcut($_.FullName)
    if ($s.TargetPath -match 'YandexBrowser\\Application\\browser.exe' -and $s.Arguments -notmatch '9229') {
        Copy-Item -LiteralPath $_.FullName "$bak\$($_.Name).$(Get-Random)" -Force
        $s.Arguments = ($flags + ' ' + $s.Arguments).Trim(); $s.Save()
        "ярлык исправлен: $($_.FullName)"
    }
}


# 4) команды открытия файлов/ссылок у пользователя rdpuser (HKCU)
$n = 0
Get-ChildItem "Registry::HKEY_USERS\$sid\Software\Classes" -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^Yandex' } | ForEach-Object {
    $k = "$($_.PSPath)\shell\open\command"
    $v = (Get-ItemProperty $k -ErrorAction SilentlyContinue).'(default)'
    if ($v -and $v -notmatch 'remote-debugging-port') {
        $new = $v.Replace("`"$exe`" ", "`"$exe`" $flags ")
        Set-ItemProperty $k -Name '(default)' -Value $new; $n++
    }
}
"HKCU команд изменено: $n"
"готово " + (Get-Date -Format s)
