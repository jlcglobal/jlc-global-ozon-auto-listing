param([string]$ProjectRoot = '')
$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) { $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
$runtime = Join-Path $ProjectRoot 'runtime'
$pidFile = Join-Path $runtime 'workbench-server.pid'
$stopFile = Join-Path $runtime 'workbench-stop-requested'
Set-Content -LiteralPath $stopFile -Value ((Get-Date).ToString('o')) -Force
$stopped = $false
if (Test-Path -LiteralPath $pidFile) {
    $pid = [int]((Get-Content -LiteralPath $pidFile -Raw).Trim())
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($proc) { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue; Write-Host ('已停止工作台 (PID ' + $pid + ')'); $stopped = $true }
    else { Write-Host '工作台进程不存在，清理 PID 文件' }
    if (Test-Path -LiteralPath $pidFile) { [System.IO.File]::Delete($pidFile) }
}
if (-not $stopped) {
    $conn = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $ids = $conn | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($id in $ids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue; Write-Host ('已按端口停止工作台 (PID ' + $id + ')') }
    } else {
        Write-Host '工作台未在运行（端口 8765 无监听）'
    }
}
