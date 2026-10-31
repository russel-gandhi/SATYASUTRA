# Start everything the demo needs, then run the preflight table. From the repo root:  .\scripts\start_demo.ps1
# Starts (each only if its port is not already answering): the TruFor + Smaug service on 8100 and the API + UI on 8000.
# Logs go to forensics\service.log and serve.log. Stop them from the Task Manager or with: Stop-Process -Id <pid>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Test-Up($url) { try { Invoke-RestMethod -Uri $url -TimeoutSec 3 | Out-Null; return $true } catch { return $false } }
function Wait-Up($url, $seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $end) { if (Test-Up $url) { return $true }; Start-Sleep -Seconds 2 }
    return $false
}

if (-not (Test-Up "http://127.0.0.1:8100/health")) {
    Write-Host "Starting the forensics service (TruFor + Smaug)..."
    Start-Process -FilePath "$root\forensics\venv311\Scripts\python.exe" -ArgumentList "forensics\service.py", "--port", "8100" `
        -RedirectStandardOutput "$root\forensics\service.log" -RedirectStandardError "$root\forensics\service.err.log" -WindowStyle Hidden
    if (-not (Wait-Up "http://127.0.0.1:8100/health" 120)) { Write-Host "The forensics service did not come up; see forensics\service.err.log" -ForegroundColor Red }
}
if (-not (Test-Up "http://127.0.0.1:8000/health")) {
    Write-Host "Starting the API and UI..."
    Start-Process -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList "scripts\serve.py", "--port", "8000" `
        -RedirectStandardOutput "$root\serve.log" -RedirectStandardError "$root\serve.err.log" -WindowStyle Hidden
    if (-not (Wait-Up "http://127.0.0.1:8000/health" 60)) { Write-Host "The API did not come up; see serve.err.log" -ForegroundColor Red }
}
& "$root\.venv\Scripts\python.exe" scripts\preflight.py --api http://127.0.0.1:8000
Write-Host "UI: http://127.0.0.1:8000/"
