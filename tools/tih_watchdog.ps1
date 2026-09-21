# Сторож TextInputHost (служба ввода Windows: экранная клавиатура, эмодзи, история буфера обмена Win+V).
# 2026-09-20/21 процесс дважды уходил в холостой цикл: 112 тыс. CPU-секунд за сутки (~3 ядра), потом снова
# ~0,5 ядра через два часа после перезапуска; свежий экземпляр спокоен (~1 % ядра), разгоняется со временем.
# Причина не установлена — поэтому кроме лечения (Stop-Process: Windows сама поднимает процесс при следующем
# вводе) пишутся улики: возраст процесса, активное окно, раскладка, счётчик изменений буфера обмена.
# Запуск — задача TextInputHostWatchdog под rdpuser (Interactive: нужны окно и раскладка сессии),
# TimeTrigger с повтором PT5M (повтор у триггера входа на этой машине не срабатывает, см. README).
$log = "W:\tools\tih_watchdog.log"
$limitPct = 25      # % одного ядра, в среднем за окно замера
$windowSec = 30
Add-Type -Namespace Tih -Name U -MemberDefinition @'
[DllImport("user32.dll")] public static extern uint GetClipboardSequenceNumber();
[DllImport("user32.dll")] public static extern System.IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(System.IntPtr h, out uint pid);
[DllImport("user32.dll")] public static extern System.IntPtr GetKeyboardLayout(uint tid);
'@
$p = Get-Process TextInputHost -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) { exit 0 }
$c0 = $p.CPU; $clip0 = [Tih.U]::GetClipboardSequenceNumber()
Start-Sleep -Seconds $windowSec
$p.Refresh(); $pct = ($p.CPU - $c0) / $windowSec * 100
if ($pct -lt $limitPct) { exit 0 }
$fg = [Tih.U]::GetForegroundWindow(); $fpid = 0; $tid = [Tih.U]::GetWindowThreadProcessId($fg, [ref]$fpid)
$fp = Get-Process -Id $fpid -ErrorAction SilentlyContinue
$hkl = "{0:x8}" -f ([Tih.U]::GetKeyboardLayout($tid).ToInt64() -band 0xffffffff)
$age = (Get-Date) - $p.StartTime
$line = "{0:yyyy-MM-dd HH:mm:ss}  {1,5:0.0}% ядра  возраст {2:0.0} ч  всего {3:0} c CPU  окно: {4} «{5}»  раскладка {6}  буфер: {7} изм. за {8} с (счётчик {9})" -f `
    (Get-Date), $pct, $age.TotalHours, $p.CPU, $fp.ProcessName, $fp.MainWindowTitle, $hkl, ([Tih.U]::GetClipboardSequenceNumber() - $clip0), $windowSec, $clip0
if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Remove-Item $log -Force }
Add-Content -Path $log -Value $line -Encoding UTF8
Stop-Process -Id $p.Id -Force
