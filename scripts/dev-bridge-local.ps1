param(
    # bridge 监听端口；后端 STORE_BRIDGE_URL 默认指向 8898
    [int]$Port = 8898
)

# 本地启动店铺 bridge（开发用）
#
# bridge 是 temu-ops 的 fork，**零依赖**（package.json 的 dependencies 是空的），
# 不需要 npm install，直接 node server.js。
#
# 关键点：
#   1. 必须带 BRIDGE_TOKEN，否则所有接口返回 403。它的值来自 .env 的
#      STORE_BRIDGE_TOKEN（后端用同一个值调 bridge）。
#   2. 必须用 PowerShell 正则解析 .env —— cmd 的 `for /f delims==` 会把值里
#      的 `=` 当分隔符截断。
#   3. 会话文件在 bridge/runtime/ 下，删了等于所有店铺都要重新登录。

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

# 读 .env
$envFile = Join-Path $Root '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"', "'"), 'Process')
        }
    }
}

$env:BRIDGE_TOKEN = "$env:STORE_BRIDGE_TOKEN"
$env:PORT = "$Port"
if (-not $env:LOG) { $env:LOG = '1' }

if (-not $env:BRIDGE_TOKEN) {
    Write-Host "缺少 STORE_BRIDGE_TOKEN（.env 里没有？），bridge 会拒绝所有请求" -ForegroundColor Yellow
}
Write-Host "bridge：port=$Port, token 长度=$($env:BRIDGE_TOKEN.Length)"

# 清理占用端口的旧实例
for ($i = 0; $i -lt 5; $i++) {
    $lines = netstat -ano | Select-String ":$Port\s.*LISTENING"
    if (-not $lines) { break }
    $oldPid = ($lines[0].ToString() -split '\s+')[-1]
    & taskkill /F /T /PID $oldPid 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "已停止占用 ${Port} 的旧进程 PID=$oldPid"
    } else {
        Write-Host "无法结束 PID=$oldPid（可能是提权会话）" -ForegroundColor Yellow
    }
    Start-Sleep -Milliseconds 800
}

Set-Location (Join-Path $Root 'bridge')
$logFile = Join-Path $Root ("data\logs\bridge-" + (Get-Date -Format 'yyyyMMdd') + ".log")
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data\logs') | Out-Null
& node server.js *>> $logFile
