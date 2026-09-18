# Автозапуск горячих клавиш A56: задача планировщика под консольным пользователем
# (WinRM работает под alena, за экраном — rdpuser; из чужого сеанса хоткеи не регистрируются).
# Запускается при входе в систему и сразу сейчас.
$ErrorActionPreference = 'Stop'
$ahk = 'W:\tools\ahk\AutoHotkey64.exe'
$script = 'W:\tools\scrcpy-win64-v3.1\a56_hotkeys.ahk'

$who = (quser 2>$null | Select-String 'console') -replace '^\s*>?\s*(\S+)\s+console.*', '$1'
$who = $who.Trim()
if (-not $who) { $who = $env:USERNAME }
$user = "$env:COMPUTERNAME\$who"

$action = New-ScheduledTaskAction -Execute $ahk -Argument ('"' + $script + '"') -WorkingDirectory (Split-Path $script)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
# ExecutionTimeLimit снимаем: задача висит постоянно, дефолтные 72 часа её бы убили
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'A56Hotkeys' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output ('задача A56Hotkeys создана для ' + $user)

Get-Process AutoHotkey64 -EA SilentlyContinue | Stop-Process -Force
Start-ScheduledTask -TaskName 'A56Hotkeys'
Start-Sleep 4
$p = Get-Process AutoHotkey64 -EA SilentlyContinue
if ($p) { Write-Output ('горячие клавиши работают, pid ' + $p.Id) }
else { Write-Output ('не запустилось, код: ' + (Get-ScheduledTaskInfo -TaskName 'A56Hotkeys').LastTaskResult) }
