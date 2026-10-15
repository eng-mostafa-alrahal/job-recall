Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  JobRecall Local Backend Service" -ForegroundColor Cyan
Write-Host "  Running at http://127.0.0.1:8765" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Cyan

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

& ".\backend\.venv\Scripts\uvicorn.exe" app.main:app --app-dir backend --host 127.0.0.1 --port 8765 --reload
