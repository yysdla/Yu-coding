$ErrorActionPreference = "Stop"

Set-Location "C:\Users\Administrator\Desktop\hermes-agent-main"

$env:HERMES_HOME = "C:\Users\Administrator\AppData\Local\hermes"
$env:PROJECTLENS_API_BASE_URL = "http://127.0.0.1:8000/api/v1"
$env:PROJECTLENS_DEFAULT_TENANT_ID = "demo"
$env:PROJECTLENS_DEFAULT_PROJECT_ID = "payment"

Write-Host "ProjectLens Hermes interactive terminal" -ForegroundColor Cyan
Write-Host "When Hermes prompt appears, run:" -ForegroundColor Yellow
Write-Host "/project What is this project?" -ForegroundColor Green
Write-Host ""

& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" "C:\Users\Administrator\Desktop\hermes-agent-main\cli.py"
