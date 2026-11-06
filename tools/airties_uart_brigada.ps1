# Консоль AirTies по UART на столе alena Бригады (06.11.2026). Заменяет airties_uart.py (на Бригаде нет Python, ставить не стали):
# одно окно — и журнал, и ввод. Ждёт переходник CP210x, пишет всё в C:\ProgramData\alena\airties_uart\консоль.log,
# клавиши из окна уходят в порт. Ядро и CFE AirTies — 115200 8N1 (getty отвечал на 9600 — параметр -Baud 9600).
# Ctrl+C уходит в порт как 0x03 (окно им не закрывается); закрыть окно — крестиком или Ctrl+Q.
# Файл «ввод» (строки добавляются в конец) работает как в старом скрипте: строка уходит в порт, «^C» = Ctrl+C.
param([int]$Baud = 115200)
$ErrorActionPreference = 'Stop'
$Dir = 'C:\ProgramData\alena\airties_uart'
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$Log = Join-Path $Dir 'консоль.log'
$In = Join-Path $Dir 'ввод'
if (-not (Test-Path $In)) { New-Item -ItemType File -Path $In | Out-Null }
$mutex = New-Object System.Threading.Mutex($false, 'Global\alena_airties_uart')
if (-not $mutex.WaitOne(0)) { Write-Host 'Консоль AirTies уже запущена в другом окне.'; Start-Sleep 5; exit }
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Console]::TreatControlCAsInput = $true
$Host.UI.RawUI.WindowTitle = 'Консоль AirTies (UART)'
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Пишем([string]$s) {
    try { [IO.File]::AppendAllText($Log, $s, $utf8) } catch {}
}
function Метка([string]$t) {
    $s = "`n=== $(Get-Date -Format 'dd.MM HH:mm:ss') $t ===`n"
    Write-Host -NoNewline $s -ForegroundColor Yellow
    Пишем $s
}
function Найти-Порт {
    $e = Get-CimInstance Win32_PnPEntity -Filter "Name like '%(COM%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'CP210|Silicon Labs|USB-SERIAL|CH340|UART' } | Select-Object -First 1
    if ($e -and $e.Name -match '\((COM\d+)\)') { return $Matches[1] }
    return $null
}

$p = $null
$поз = (Get-Item $In).Length
Метка "консоль запущена, жду переходник ($Baud)"
while ($true) {
    if ($null -eq $p -or -not $p.IsOpen) {
        $имя = Найти-Порт
        if ($имя) {
            try {
                $p = New-Object System.IO.Ports.SerialPort $имя, $Baud, 'None', 8, 'One'
                $p.Encoding = $utf8; $p.ReadTimeout = 200; $p.WriteTimeout = 1000
                $p.DtrEnable = $true; $p.RtsEnable = $true
                $p.Open()
                Метка "модуль на связи: $имя, $Baud"
            } catch { $p = $null; Start-Sleep 1 }
        } else { Start-Sleep 1 }
        continue
    }
    try {
        $s = $p.ReadExisting()
        if ($s) { Write-Host -NoNewline $s; Пишем $s }
        while ([Console]::KeyAvailable) {
            $k = [Console]::ReadKey($true)
            $ctrl = ($k.Modifiers -band [ConsoleModifiers]::Control) -ne 0
            if ($ctrl -and $k.Key -eq 'Q') { $p.Close(); exit }
            elseif ($ctrl -and $k.Key -eq 'C') { $p.Write([byte[]](3), 0, 1) }
            elseif ($ctrl -and $k.Key -eq 'D') { $p.Write([byte[]](4), 0, 1) }
            elseif ($k.Key -eq 'Enter') { $p.Write("`r") }
            elseif ($k.Key -eq 'Backspace') { $p.Write([byte[]](127), 0, 1) }
            elseif ($k.Key -eq 'Tab') { $p.Write("`t") }
            elseif ($k.Key -eq 'UpArrow') { $p.Write("$([char]27)[A") }
            elseif ($k.Key -eq 'DownArrow') { $p.Write("$([char]27)[B") }
            elseif ($k.Key -eq 'RightArrow') { $p.Write("$([char]27)[C") }
            elseif ($k.Key -eq 'LeftArrow') { $p.Write("$([char]27)[D") }
            elseif ($k.KeyChar -ne [char]0) { $p.Write([string]$k.KeyChar) }
        }
        $д = [IO.File]::ReadAllBytes($In)
        if ($д.Length -gt $поз -and $д[-1] -eq 10) {
            $текст = $utf8.GetString($д, $поз, $д.Length - $поз)
            foreach ($стр in ($текст -split "`n")) {
                $стр = $стр.TrimEnd("`r")
                if ($стр -eq '') { continue }
                if ($стр -eq '^C') { $p.Write([byte[]](3), 0, 1) } else { $p.Write($стр + "`r") }
                Пишем "`n>>> $стр`n"
            }
            $поз = $д.Length
        }
    } catch [TimeoutException] {
    } catch {
        try { $p.Close() } catch {}
        $p = $null
        Метка 'модуль пропал — жду'
        Start-Sleep 1
    }
    Start-Sleep -Milliseconds 30
}
