$Root = Split-Path -Parent $PSScriptRoot
foreach ($line in Get-Content (Join-Path $Root '.env')) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"', "'"), 'Process')
    }
}
# 启动前清理同端口的旧实例：
# uvicorn --reload 在 Windows 上被杀父进程后，multiprocessing 的 worker 会继续占着端口，
# 结果新实例静默启动失败、旧代码继续响应——表现为「改了代码没生效」或「两个进程抢端口」。
$Port = 8089
for ($i = 0; $i -lt 5; $i++) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { break }
    $oldPid = $conn.OwningProcess
    # taskkill 比 Stop-Process 强：能杀掉不同会话/权限下的进程
    & taskkill /F /T /PID $oldPid 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "已停止占用 ${Port} 的旧进程 PID=$oldPid"
    } else {
        Write-Host "无法结束 PID=$oldPid（可能需要管理员权限），等待端口释放"
    }
    Start-Sleep -Milliseconds 800
}

Set-Location (Join-Path $Root 'backend')
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload *>> (Join-Path $Root 'data\logs\dev-backend.log')