# 清理本地后端端口占用（需要管理员权限）
#
# 背景：Windows 上 `uvicorn --reload` 的父进程被杀后，multiprocessing 的 worker
# 会继续占着端口。如果这个 worker 是在提权会话里起的，普通用户权限的
# Stop-Process / taskkill 都杀不掉，表现为：
#   - 新实例启动失败，旧代码继续响应（改了代码没生效）
#   - 旧 worker 没读到新的 .env（报「未配置 CREDENTIAL_KEY」「店铺管理服务未配置」）
#
# 用法：右键「以管理员身份运行 PowerShell」，然后执行本脚本。

$ErrorActionPreference = 'SilentlyContinue'
$ports = @(8089, 8090, 8091)

Write-Host "== 清理本地后端端口占用 ==" -ForegroundColor Cyan

foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen
    if (-not $conns) {
        Write-Host "  ${port}: 未被占用"
        continue
    }
    foreach ($conn in $conns) {
        $procId = $conn.OwningProcess
        $proc = Get-Process -Id $procId
        if ($proc) {
            Write-Host "  ${port}: 结束 PID=$procId ($($proc.ProcessName))"
            & taskkill /F /T /PID $procId 2>&1 | Out-Null
        } else {
            Write-Host "  ${port}: PID=$procId 在进程表中查不到（提权会话），尝试 wmic 终止" -ForegroundColor Yellow
            & wmic process where "processid=$procId" delete 2>&1 | Out-Null
        }
    }
}

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "== 残留检查 ==" -ForegroundColor Cyan
$left = $false
foreach ($port in $ports) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen) {
        Write-Host "  ${port}: 仍占用（如果 wmic 也失败，请重启机器）" -ForegroundColor Red
        $left = $true
    }
}

if (-not $left) {
    Write-Host "  端口已全部释放" -ForegroundColor Green
    Write-Host ""
    Write-Host "现在可以启动后端：" -ForegroundColor Green
    Write-Host "  .\scripts\dev-backend-local.ps1"
}
