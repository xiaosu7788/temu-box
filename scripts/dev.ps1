$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $ProjectRoot 'backend'
$Frontend = Join-Path $ProjectRoot 'frontend'

Start-Process pwsh -ArgumentList '-NoExit', '-Command', "Set-Location '$Backend'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8089 --reload"
Start-Process pwsh -ArgumentList '-NoExit', '-Command', "Set-Location '$Frontend'; npm run dev"
