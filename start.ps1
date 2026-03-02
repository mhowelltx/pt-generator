# start.ps1 — Start the PT Generator web app
# Usage: .\start.ps1 [-Port 8000] [-NoReload]

param(
    [int]$Port = 8000,
    [switch]$NoReload
)

if (-not (Test-Path ".env")) {
    Write-Warning ".env file not found."
    Write-Warning "Create one with: echo ANTHROPIC_API_KEY=your_key_here > .env"
    exit 1
}

$reloadFlag = if ($NoReload) { "" } else { "--reload" }

Write-Host "Starting PT Generator on http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop.`n" -ForegroundColor Cyan

python -m uvicorn app.web.server:app --host 127.0.0.1 --port $Port $reloadFlag
