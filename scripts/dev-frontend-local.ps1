$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $Root 'frontend')
& 'C:\Program Files\nodejs\npm.cmd' run dev *>> (Join-Path $Root 'data\logs\dev-frontend.log')