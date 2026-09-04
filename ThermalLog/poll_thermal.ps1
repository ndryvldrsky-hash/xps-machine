$logDir = "W:\ThermalLog"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir "thermal_log.csv"
if (-not (Test-Path $logFile)) {
    "Timestamp,TZ00_C,TZ01_C,CPU_LoadPercent,FanRPM" | Out-File -FilePath $logFile -Encoding utf8
}

$retentionHours = 6
$trimEveryN = 60
$counter = 0

while ($true) {
    $zones = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue
    $tz00 = ($zones | Where-Object { $_.InstanceName -match "TZ00" } | Select-Object -First 1).CurrentTemperature
    $tz01 = ($zones | Where-Object { $_.InstanceName -match "TZ01" } | Select-Object -First 1).CurrentTemperature
    $tz00c = if ($tz00) { [math]::Round(($tz00/10)-273.15,1) } else { "" }
    $tz01c = if ($tz01) { [math]::Round(($tz01/10)-273.15,1) } else { "" }

    $load = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average

    $fan = Get-CimInstance Win32_Fan -ErrorAction SilentlyContinue | Select-Object -First 1
    $fanRpm = if ($fan -and $fan.DesiredSpeed) { $fan.DesiredSpeed } else { "" }

    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts,$tz00c,$tz01c,$load,$fanRpm" | Out-File -FilePath $logFile -Append -Encoding utf8

    $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
    $json = [ordered]@{
        timestamp = $ts
        tz00_c = $tz00c
        tz01_c = $tz01c
        cpu_load_percent = $load
        last_boot = $boot.ToString("yyyy-MM-ddTHH:mm:ss")
    } | ConvertTo-Json -Compress
    $json | Out-File -FilePath (Join-Path $logDir "latest.json") -Encoding utf8

    $counter++
    if ($counter -ge $trimEveryN) {
        $counter = 0
        $cutoff = (Get-Date).AddHours(-$retentionHours)
        $lines = Get-Content $logFile
        if ($lines.Count -gt 1) {
            $header = $lines[0]
            $kept = @($lines[1..($lines.Count-1)] | Where-Object {
                $parts = $_ -split ","
                if ($parts.Count -ge 1 -and $parts[0]) {
                    try { [datetime]$parts[0] -gt $cutoff } catch { $true }
                } else { $true }
            })
            @($header) + $kept | Set-Content $logFile -Encoding utf8
        }
    }

    Start-Sleep -Seconds 1
}
