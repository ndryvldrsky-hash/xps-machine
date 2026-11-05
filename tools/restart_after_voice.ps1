# Перезапуск диктовки, когда озвучка затихла: последняя строка про ответ/место/нампад в журнале старше 25 с (ждём до 10 мин).
# 06.11.2026: перезапуск по часам оборвал ответ на середине.
$log = 'W:\Tools\dictate\dictate.log'
$deadline = (Get-Date).AddMinutes(10)
while ((Get-Date) -lt $deadline) {
    $last = Get-Content $log -Tail 200 -Encoding UTF8 | Where-Object { $_ -match 'место →|ответ голосом|нампад|клавиша меню|правый Alt' } | Select-Object -Last 1
    $quiet = $true
    if ($last -and $last.Length -ge 19) {
        $t = [datetime]::ParseExact($last.Substring(0,19), 'yyyy-MM-dd HH:mm:ss', $null)
        if (((Get-Date) - $t).TotalSeconds -lt 25) { $quiet = $false }
    }
    if ($quiet) { break }
    Start-Sleep 5
}
& W:\Tools\dictate\restart_once.ps1
