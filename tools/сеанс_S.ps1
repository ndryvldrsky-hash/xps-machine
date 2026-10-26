# Выполнить код в сеансе rdpuser (задача AlenaSessW) и вернуть вывод. Подключать: . "$env:ProgramData\alena\S.ps1"
function S { param([string]$code,[int]$wait=60)
  $d="$env:ProgramData\alena"; $f="$d\sess_cmd.ps1"; $o="$d\sess_out.txt"; Remove-Item $o -EA 0
  [IO.File]::WriteAllText($f, "& {`r`n$code`r`n} *>&1 | Out-File -Encoding utf8 '$o'; 'done' | Out-File -Append -Encoding utf8 '$o'", (New-Object Text.UTF8Encoding $true))
  if (-not (Get-ScheduledTask AlenaSessW -EA 0)) {   # задача-исполнитель: создаётся при первом вызове
    $a=New-ScheduledTaskAction -Execute conhost.exe -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$f`""
    $p=New-ScheduledTaskPrincipal -UserId rdpuser -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName AlenaSessW -Action $a -Principal $p | Out-Null }
  Start-ScheduledTask -TaskName AlenaSessW
  for($i=0;$i -lt $wait;$i++){ Start-Sleep 1; if((Test-Path $o) -and (Get-Content $o -Raw) -match 'done\s*$'){break} }
  Get-Content $o
}
