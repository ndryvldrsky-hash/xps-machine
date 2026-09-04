$out = "W:\ThermalLog\lhm_probe.txt"
"probe started $(Get-Date)" | Out-File $out
try {
    Add-Type -Path "W:\ThermalLog\LHM\LibreHardwareMonitorLib.dll"
    $computer = New-Object LibreHardwareMonitor.Hardware.Computer
    $computer.IsCpuEnabled = $true
    $computer.IsMotherboardEnabled = $true
    $computer.IsControllerEnabled = $true
    $computer.Open()
    foreach ($hw in $computer.Hardware) {
        $hw.Update()
        "HW: $($hw.Name) [$($hw.HardwareType))]" | Out-File $out -Append
        foreach ($sensor in $hw.Sensors) {
            "  sensor: $($sensor.Name) type=$($sensor.SensorType) value=$($sensor.Value)" | Out-File $out -Append
        }
        foreach ($sub in $hw.SubHardware) {
            $sub.Update()
            "  SUB: $($sub.Name)" | Out-File $out -Append
            foreach ($sensor in $sub.Sensors) {
                "    sensor: $($sensor.Name) type=$($sensor.SensorType) value=$($sensor.Value)" | Out-File $out -Append
            }
        }
    }
    $computer.Close()
    "probe finished ok" | Out-File $out -Append
} catch {
    "EXCEPTION: $($_.Exception.ToString())" | Out-File $out -Append
}
