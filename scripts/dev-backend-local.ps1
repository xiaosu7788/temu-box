param(
    # 后端端口；8089 被本机其它进程占用时可传 -Port 8091
    [int]$Port = 8089
)

# 本地启动后端（开发用）
#
# 三件事，缺一不可：
#   1. 读 .env 注入环境变量 —— 注意必须用 PowerShell 的正则解析，
#      不能用 cmd 的 `for /f delims==`：CREDENTIAL_KEY 是 base64，
#      结尾常带 `=` 填充，用 `=` 做分隔符会把它截断，导致
#      credential_cipher 判定「未配置」，记住密码功能失效。
#   2. 清理占用端口的旧实例 —— uvicorn --reload 的父进程被杀后，
#      multiprocessing worker 会继续占端口，新实例静默启动失败。
#   3. 启动 uvicorn。

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

# 1. 读 .env（正则是贪婪匹配，保留值里的 `=`）
$envFile = Join-Path $Root '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"', "'"), 'Process')
        }
    }
}
$keyLen = ($env:CREDENTIAL_KEY | Measure-Object -Character).Characters
Write-Host "配置：CREDENTIAL_KEY 长度=$keyLen（44 表示正常）, bridge=$($env:STORE_BRIDGE_URL)"

# 2. 清理占用端口的旧实例
for ($i = 0; $i -lt 5; $i++) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { break }
    $oldPid = $conn.OwningProcess
    & taskkill /F /T /PID $oldPid 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "已停止占用 ${Port} 的旧进程 PID=$oldPid"
    } else {
        Write-Host "无法结束 PID=$oldPid（可能是提权会话），请用管理员权限跑 scripts/kill-backend-port.ps1" -ForegroundColor Yellow
    }
    Start-Sleep -Milliseconds 800
}

# 3. 启动
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data\logs') | Out-Null
Set-Location (Join-Path $Root 'backend')
# 日志按天分文件：固定文件名会被上一个实例占住，导致新实例启动即失败
$logFile = Join-Path $Root ("data\logs\dev-backend-" + (Get-Date -Format 'yyyyMMdd') + ".log")
# --reload-dir app：只监控应用代码。默认监控整个 backend/ 时，
# 改测试或日志文件也会触发 reload，正在跑的任务会被打断。
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload --reload-dir app *>> $logFile
