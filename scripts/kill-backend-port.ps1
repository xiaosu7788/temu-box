# 清理本地后端端口占用（建议用管理员权限运行）
#
# 背景：Windows 上 uvicorn --reload 的父进程被杀后，multiprocessing 的 worker
# 会继续占着端口。如果这个 worker 在提权会话里，普通权限的 taskkill 也杀不掉。
#
# 用法：右键「以管理员身份运行」PowerShell，然后执行本脚本。
#
# 注意：这里刻意不用 Get-NetTCPConnection —— 它在没有匹配结果时会抛
# CIM 异常（而不是返回空数组），-ErrorAction SilentlyContinue 也挡不住。

$ErrorActionPreference = 'SilentlyContinue'
$ports = @(8089, 8090, 8091, 5173)

Write-Host "== 清理本地后端端口占用 ==" -ForegroundColor Cyan

foreach ($port in $ports) {
    # netstat 不会抛异常，稳定用于探测
    $lines = netstat -ano | Select-String ":$port\s.*LISTENING"
    if (-not $lines) {
        Write-Host "  ${port}: not used"
        continue
    }
    foreach ($line in $lines) {
        $procId = ($line.ToString() -split '\s+')[-1]
        if (-not $procId -or $procId -eq '0') { continue }
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  ${port}: killing PID=$procId ($($proc.ProcessName))"
            taskkill /F /T /PID $procId 2>&1 | Out-Null
        } else {
            Write-Host "  ${port}: PID=$procId not visible in this session (elevated?), trying wmic" -ForegroundColor Yellow
            wmic process where "processid=$procId" delete 2>&1 | Out-Null
        }
    }
}

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "== 残留检查 ==" -ForegroundColor Cyan
$left = $false
foreach ($port in $ports) {
    if (netstat -ano | Select-String ":$port\s.*LISTENING") {
        Write-Host "  ${port}: STILL LISTENING - if wmic also failed, reboot is the only fix" -ForegroundColor Red
        $left = $true
    } else {
        Write-Host "  ${port}: free"
    }
}

if (-not $left) {
    Write-Host ""
    Write-Host "端口已全部释放，现在可以启动后端：" -ForegroundColor Green
    Write-Host "  .\scripts\dev-backend-local.ps1"
}
