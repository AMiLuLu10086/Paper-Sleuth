param()
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run .\install.ps1 first.' }
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Build the frontend first.' }
$serverInfo = & $pythonExe -c 'from backend.app.config import load_settings; c=load_settings(); print(c.server.host); print(c.server.port)'
if ($LASTEXITCODE -ne 0) { throw 'Invalid config.yaml.' }
$listenHost = $serverInfo[0]
$listenPort = $serverInfo[1]
$probeHost = $listenHost.Trim([char[]]'[]')
if ($probeHost -eq '0.0.0.0') { $probeHost = '127.0.0.1' }
if ($probeHost -eq '::') { $probeHost = '::1' }
$browserHost = $probeHost
if ($browserHost.Contains(':')) { $browserHost = "[$browserHost]" }
$serverUrl = "http://${browserHost}:${listenPort}"

# Check before uvicorn opens the local Qdrant storage, which allows one process only.
$portInUse = $false
$probe = [System.Net.Sockets.TcpClient]::new()
try {
    $connection = $probe.ConnectAsync($probeHost, [int]$listenPort)
    if ($connection.Wait(2000)) { $portInUse = $probe.Connected }
} catch {
    # A refused connection means no server is listening yet.
} finally {
    $probe.Dispose()
}
if ($portInUse) {
    $health = $null
    try {
        $health = Invoke-RestMethod -Uri "$serverUrl/api/health" -TimeoutSec 5
    } catch {
        # Another application or a server still starting may occupy the port.
    }
    if ($health.status -eq 'ok' -and $health.local_processing -eq $true) {
        Write-Host "PaperSleuth is already running at $serverUrl."
        Write-Host 'Use the existing server. To restart, press Ctrl+C in its original terminal first.'
        return
    }
    throw "Port $listenPort is already in use, or PaperSleuth is still starting. Wait and retry, or check the existing server terminal."
}

Write-Host "Open $serverUrl in your browser."
Write-Host 'Keep this terminal open. Press Ctrl+C to stop.'
& $pythonExe -m uvicorn backend.app.main:app --host $listenHost --port $listenPort --no-access-log
if ($LASTEXITCODE -ne 0) { throw 'Server startup or execution failed. Check the error above.' }
