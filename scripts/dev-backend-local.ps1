$Root = Split-Path -Parent $PSScriptRoot
foreach ($line in Get-Content (Join-Path $Root '.env')) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"', "'"), 'Process')
    }
}
Set-Location (Join-Path $Root 'backend')
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8089 --reload *>> (Join-Path $Root 'data\logs\dev-backend.log')