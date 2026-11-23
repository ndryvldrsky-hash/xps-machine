# Сторож живых обоев XPS (23.11.2026). Хозяин: «Игра Соник остановилась» → «надо выяснить, что за проблемы с аппаратным
# декодированием». Причина не GPU: Lively запускает mpv с --loop-file --keep-open (ключи mpv в Lively не задать, --no-config);
# живой RTSP-поток кончается (перезапуск партии Ксоникса, обрыв источника), mpv встаёт в eof-reached=true и пытается перемотать
# «файл» на начало — «Cannot seek in this stream» сотни тысяч раз в журнал Lively, на экране последний кадр.
# Раз в 5 с опрашиваем каждый mpv по его каналу управления (\\.\pipe\mpvsocket*, открывает Lively; доступен только учётке
# сеанса — поэтому задача AlenaOboiStorozh на группу AlenaStol); eof-reached=true и адрес rtsp:// — loadfile того же адреса.
# Журнал перезагрузок — W:\ProgramData\alena\oboi\storozh.log
$log = 'W:\ProgramData\alena\oboi\storozh.log'
function Спросить($w, $r, $cmd) {
    $w.WriteLine($cmd)
    $t = $r.ReadLineAsync(); if ($t.Wait(1500)) { return $t.Result } else { return $null }
}
while ($true) {
    foreach ($p in ([IO.Directory]::GetFiles('\\.\pipe\') | Where-Object { $_ -match 'mpvsocket' } | ForEach-Object { $_ -replace '^.*\\', '' })) {
        try {
            $c = New-Object IO.Pipes.NamedPipeClientStream('.', $p, [IO.Pipes.PipeDirection]::InOut)
            $c.Connect(1500)
            $w = New-Object IO.StreamWriter($c); $w.AutoFlush = $true; $r = New-Object IO.StreamReader($c)
            $eof = Спросить $w $r '{"command":["get_property","eof-reached"],"request_id":1}'
            while ($eof -and $eof -notmatch '"request_id":1') { $eof = $r.ReadLine() }   # пропустить события mpv
            if ($eof -match '"data":true') {
                $путь = Спросить $w $r '{"command":["get_property","path"],"request_id":2}'
                while ($путь -and $путь -notmatch '"request_id":2') { $путь = $r.ReadLine() }
                if ($путь -match '"data":"(rtsp://[^"]+)"') {
                    $w.WriteLine('{"command":["loadfile","' + $Matches[1] + '","replace"],"request_id":3}')
                    Add-Content $log ("{0} перезагружен {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Matches[1]) -Encoding UTF8
                }
            }
            $c.Close()
        } catch { }
    }
    Start-Sleep 5
}
