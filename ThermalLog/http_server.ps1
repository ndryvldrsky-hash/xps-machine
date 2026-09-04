$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://+:8090/thermal/")
$listener.Start()
while ($listener.IsListening) {
    try {
        $context = $listener.GetContext()
        $response = $context.Response
        $body = Get-Content -Path "W:\ThermalLog\latest.json" -Raw -ErrorAction SilentlyContinue
        if (-not $body) { $body = "{}" }
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
        $response.ContentType = "application/json"
        $response.ContentLength64 = $bytes.Length
        $response.OutputStream.Write($bytes, 0, $bytes.Length)
        $response.OutputStream.Close()
    } catch {
        Start-Sleep -Seconds 1
    }
}
