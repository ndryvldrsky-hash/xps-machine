# Сторож процессов XPS (2026-09-24, просьба пользователя «контролировать процессы, кроме как перезапусками»).
# Запуск — задача планировщика ProcGuard от SYSTEM раз в минуту (PID меняются после перезапусков, поэтому правила
# применяются заново каждый раз по имени и командной строке). Журнал W:\tools\proc_guard\proc_guard.log — только когда
# что-то поменялось или сработало.
#   • приоритеты: диктовка (dictate.py) — High, ffmpeg камеры (dshow) — AboveNormal, служба DeviceAssociationService — Idle;
#   • DeviceAssociationService (зацикливается на «endpoint discovery failure», ела 70–230 % ядра): режим эффективности
#     (EcoQoS) и жёсткий потолок CPU через Job Object — 3 % всей машины (~0,25 ядра из 8 логических; 10 % давали 0,8 ядра);
#   • страховка: ffmpeg камеры > 3 ГБ частной памяти — запись и перезапуск потока (очереди выходов ограничены в
#     webcam_push.ps1, так что срабатывать не должно).
# Defender (MsMpEng) — защищённый процесс: ни приоритет, ни лимит к нему не применить.
$ErrorActionPreference = "Continue"
$dir = "W:\tools\proc_guard"; $log = "$dir\proc_guard.log"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
function Log($s) {
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Remove-Item $log -Force }
    Add-Content -Path $log -Value ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "  " + $s) -Encoding UTF8
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Guard {
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr a, string name);
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern IntPtr OpenJobObject(uint access, bool inherit, string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int cls, ref CPU_RATE info, int len);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr proc);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool IsProcessInJob(IntPtr proc, IntPtr job, out bool result);
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetProcessInformation(IntPtr proc, int cls, ref POWER_THROTTLING info, int len);
    [StructLayout(LayoutKind.Sequential)] struct CPU_RATE { public uint ControlFlags; public uint CpuRate; }
    [StructLayout(LayoutKind.Sequential)] struct POWER_THROTTLING { public uint Version; public uint ControlMask; public uint StateMask; }
    const uint PROCESS_ALL = 0x1F0FFF;
    // Потолок CPU: процесс в именованном Job Object с JOB_OBJECT_CPU_RATE_CONTROL_ENABLE|HARD_CAP; CpuRate — сотые доли
    // процента всей машины. Задание живёт, пока в нём есть процессы (закрытие дескриптора его не удаляет).
    // Возврат: 0 — уже был в задании, 1 — добавлен, <0 — ошибка (код Win32 со знаком минус).
    public static int CapCpu(int pid, string jobName, uint percent) {
        // имя задания исчезает, когда сторож закрывает дескриптор (само задание живёт, пока в нём процесс), поэтому
        // «уже в задании» проверяется по любому заданию — иначе служба каждую минуту вкладывалась в новое
        IntPtr p0 = OpenProcess(0x1000, false, pid);   // PROCESS_QUERY_LIMITED_INFORMATION
        if (p0 != IntPtr.Zero) { bool any; bool ok = IsProcessInJob(p0, IntPtr.Zero, out any); CloseHandle(p0); if (ok && any) return 0; }
        IntPtr job = OpenJobObject(0x1F001F, false, jobName);
        if (job == IntPtr.Zero) job = CreateJobObject(IntPtr.Zero, jobName);
        if (job == IntPtr.Zero) return -Marshal.GetLastWin32Error();
        CPU_RATE r = new CPU_RATE(); r.ControlFlags = 0x1 | 0x4; r.CpuRate = percent * 100;
        if (!SetInformationJobObject(job, 15, ref r, Marshal.SizeOf(r))) { int e = Marshal.GetLastWin32Error(); CloseHandle(job); return -e; }
        IntPtr p = OpenProcess(PROCESS_ALL, false, pid);
        if (p == IntPtr.Zero) { int e = Marshal.GetLastWin32Error(); CloseHandle(job); return -e; }
        try {
            bool inJob; IsProcessInJob(p, job, out inJob);
            if (inJob) return 0;
            if (!AssignProcessToJobObject(job, p)) return -Marshal.GetLastWin32Error();
            return 1;
        } finally { CloseHandle(p); CloseHandle(job); }
    }
    // Режим эффективности (EcoQoS): ProcessPowerThrottling, PROCESS_POWER_THROTTLING_EXECUTION_SPEED
    public static bool Eco(int pid) {
        IntPtr p = OpenProcess(0x0200, false, pid);   // PROCESS_SET_INFORMATION
        if (p == IntPtr.Zero) return false;
        try { POWER_THROTTLING t = new POWER_THROTTLING(); t.Version = 1; t.ControlMask = 1; t.StateMask = 1; return SetProcessInformation(p, 4, ref t, Marshal.SizeOf(t)); }
        finally { CloseHandle(p); }
    }
}
"@

function Set-Prio($proc, $want, $label) {
    try {
        $p = Get-Process -Id $proc.ProcessId -ErrorAction Stop
        if ($p.PriorityClass -ne $want) { $p.PriorityClass = $want; Log "$label (PID $($proc.ProcessId)): приоритет → $want" }
    } catch { Log "$label (PID $($proc.ProcessId)): приоритет не выставлен — $($_.Exception.Message)" }
}

$procs = Get-CimInstance Win32_Process

# диктовка — никогда не должна задыхаться (24.09 висела 16 мин при CPU 100 %)
$procs | ? { $_.Name -like "python*" -and $_.CommandLine -match "dictate\.py" } | % { Set-Prio $_ "High" "диктовка" }

# ffmpeg камеры — выше обычного; страховка по памяти
foreach ($f in ($procs | ? { $_.Name -eq "ffmpeg.exe" -and $_.CommandLine -match "dshow" })) {
    Set-Prio $f "AboveNormal" "ffmpeg камеры"
    $mb = [int]((Get-Process -Id $f.ProcessId -ErrorAction SilentlyContinue).PrivateMemorySize64 / 1MB)
    if ($mb -gt 3072) {
        Log "ffmpeg камеры (PID $($f.ProcessId)): $mb МБ > 3 ГБ — перезапуск потока"
        Stop-ScheduledTask -TaskName WebcamPush -ErrorAction SilentlyContinue
        Stop-Process -Id $f.ProcessId -Force -ErrorAction SilentlyContinue
        Start-Sleep 2; Start-ScheduledTask -TaskName WebcamPush
    }
}

# DeviceAssociationService: Idle + EcoQoS; БЕЗ жёсткого потолка CPU.
# 24.09 23:30: потолок 3 % через Job Object оказался ошибкой — служба крутит бесконечный поиск пропавшего устройства,
# с потолком не успевала разбирать свою очередь и раздулась до 32 ГБ памяти: кончился файл подкачки, WinRM не стартовал,
# камера не открылась. Теперь — только низкий приоритет и режим эффективности, плюс страховка по памяти: > 500 МБ →
# процесс службы завершается (Windows поднимает службу заново). Уже попавший в задание процесс лечится только перезапуском.
$svc = Get-CimInstance Win32_Service -Filter "Name='DeviceAssociationService'"
if ($svc -and $svc.ProcessId) {
    $sp = $procs | ? { $_.ProcessId -eq $svc.ProcessId }
    if ($sp) {
        Set-Prio $sp "Idle" "DeviceAssociationService"
        $mb = [int]((Get-Process -Id $svc.ProcessId -ErrorAction SilentlyContinue).PrivateMemorySize64 / 1MB)
        if ($mb -gt 500) {
            Log "DeviceAssociationService (PID $($svc.ProcessId)): $mb МБ > 500 — перезапуск процесса службы"
            Stop-Process -Id $svc.ProcessId -Force -ErrorAction SilentlyContinue
        } else {
            $flag = "$dir\eco_$($svc.ProcessId).flag"
            if (-not (Test-Path $flag)) {
                if ([Guard]::Eco([int]$svc.ProcessId)) { Log "DeviceAssociationService (PID $($svc.ProcessId)): режим эффективности" }
                Set-Content $flag "1"; Get-ChildItem "$dir\eco_*.flag" | ? { $_.Name -ne "eco_$($svc.ProcessId).flag" } | Remove-Item -Force
            }
        }
    }
}
