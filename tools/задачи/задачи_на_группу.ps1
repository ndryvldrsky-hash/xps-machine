# Задачи XPS: principal rdpuser → локальная группа AlenaStol (Грамота Премудрой 23.11.2026; хозяин: «Сначала отвязать задачи»).
# Зачем: стол можно будет пересадить на другую учётку (доменную) одной сменой состава группы, а не правкой 45 задач.
# Группа действует по пропуску входа — после перевода участнику нужно перевойти. Задача на группу идёт в сеансе ОДНОГО
# вошедшего участника (проверено на Бригаде 23.11), поэтому в группе держать одну учётку стола.
# MediaMTX — отдельно: под SYSTEM при старте системы (экран ему не нужен), камера не ждёт входа.
# Откат: XML в W:\ProgramData\alena\задачи_rdpuser_2026-11-23\ (копия — Запасник Штаба, .local/xps_задачи/).
#   powershell -File задачи_на_группу.ps1            — сухой прогон (что изменится)
#   powershell -File задачи_на_группу.ps1 -Сделать   — перевести
param([switch]$Сделать)
$ErrorActionPreference = 'Stop'
$Группа = 'AlenaStol'
$ns = 'http://schemas.microsoft.com/windows/2004/02/mit/task'

if ($Сделать -and -not (Get-LocalGroup $Группа -ErrorAction SilentlyContinue)) {
    New-LocalGroup $Группа -Description 'Учётка стола XPS: задачи идут в её сеансе' | Out-Null
    "группа $Группа создана"
}
if ($Сделать -and -not (Get-LocalGroupMember $Группа -ErrorAction SilentlyContinue | ? Name -match '\\rdpuser$')) {
    Add-LocalGroupMember $Группа -Member rdpuser; "rdpuser — в группе $Группа"
}

$задачи = Get-ScheduledTask | ? { $_.Principal.UserId -match 'rdpuser' -and $_.TaskPath -notmatch 'SoftLanding' -and $_.TaskName -notmatch 'OneDrive' }
foreach ($t in $задачи) {
    $имя = "$($t.TaskPath)$($t.TaskName)"
    [xml]$x = Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath
    $m = New-Object Xml.XmlNamespaceManager $x.NameTable; $m.AddNamespace('t', $ns)
    $pr = $x.SelectSingleNode('//t:Principals/t:Principal', $m)
    $lt = $pr.SelectSingleNode('t:LogonType', $m)
    if ($lt -and $lt.InnerText -notin @('InteractiveToken')) { "ПРОПУСК $имя — LogonType $($lt.InnerText) (не интерактивная)"; continue }

    if ($t.TaskName -eq 'MediaMTX') {
        # под SYSTEM, при старте системы + прежние триггеры
        foreach ($n in @('t:UserId', 't:LogonType', 't:GroupId')) { $e = $pr.SelectSingleNode($n, $m); if ($e) { [void]$pr.RemoveChild($e) } }
        $u = $x.CreateElement('UserId', $ns); $u.InnerText = 'S-1-5-18'; [void]$pr.PrependChild($u)
        $rl = $pr.SelectSingleNode('t:RunLevel', $m); if (-not $rl) { $rl = $x.CreateElement('RunLevel', $ns); [void]$pr.AppendChild($rl) }; $rl.InnerText = 'HighestAvailable'
        $tr = $x.SelectSingleNode('//t:Triggers', $m)
        foreach ($e in @($tr.SelectNodes('t:LogonTrigger', $m))) { [void]$tr.RemoveChild($e) }
        if (-not $tr.SelectSingleNode('t:BootTrigger', $m)) { $b = $x.CreateElement('BootTrigger', $ns); $en = $x.CreateElement('Enabled', $ns); $en.InnerText = 'true'; [void]$b.AppendChild($en); [void]$tr.AppendChild($b) }
        $что = 'SYSTEM, при старте системы'
    } else {
        foreach ($n in @('t:UserId', 't:LogonType')) { $e = $pr.SelectSingleNode($n, $m); if ($e) { [void]$pr.RemoveChild($e) } }
        if (-not $pr.SelectSingleNode('t:GroupId', $m)) { $g = $x.CreateElement('GroupId', $ns); $g.InnerText = $Группа; [void]$pr.PrependChild($g) }
        # триггер входа — «любой пользователь»: задача всё равно пойдёт только у участника группы
        foreach ($e in @($x.SelectNodes('//t:Triggers/t:LogonTrigger/t:UserId', $m))) { [void]$e.ParentNode.RemoveChild($e) }
        $что = "группа $Группа"
    }
    if (-not $Сделать) { "будет: $имя [$($t.State)] → $что"; continue }
    try {
        Register-ScheduledTask -Xml $x.OuterXml -TaskName $t.TaskName -TaskPath $t.TaskPath -Force | Out-Null
        $нов = Get-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath
        "готово: $имя → user=$($нов.Principal.UserId) group=$($нов.Principal.GroupId) [$($нов.State)]"
    } catch { "ОШИБКА $имя : $($_.Exception.Message)" }
}
