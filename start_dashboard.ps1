# Portable launcher for Prop-Panel dashboard.
# Usage: right-click -> Run with PowerShell, or:
#   powershell -ExecutionPolicy Bypass -File .\start_dashboard.ps1

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DashDir = Join-Path $Root "dashboard"
$HostName = "127.0.0.1"
$Port = 63000
$App = Join-Path $DashDir "app.py"

if (-not (Test-Path $App)) {
    Write-Error "Dashboard not found: $App"
    exit 1
}

Set-Location $DashDir
Write-Host "Starting Prop-Panel dashboard at http://${HostName}:$Port"
Write-Host "Repo root: $Root"
Write-Host "Does NOT start/stop MetaTrader. Configure dashboard\terminals.json"
Write-Host "Ctrl+C to stop."
python $App
