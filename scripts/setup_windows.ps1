[CmdletBinding()]
param(
    [switch]$SetupOnly,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Require-Winget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "未找到 winget。请先从 Microsoft Store 安装或更新 App Installer，然后重新运行。"
    }
}

function Install-PackageIfMissing {
    param([string]$Command, [string]$PackageId, [string]$Label)
    if (Get-Command $Command -ErrorAction SilentlyContinue) { return }
    Write-Host "正在安装 $Label ..." -ForegroundColor Cyan
    winget install --id $PackageId --exact --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) { throw "$Label 安装失败，winget 退出码：$LASTEXITCODE" }
    Refresh-Path
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "$Label 已安装但当前终端尚未识别。请关闭 Codex，重新打开后再次执行本脚本。"
    }
}

Require-Winget
Install-PackageIfMissing "git" "Git.Git" "Git"
Install-PackageIfMissing "python" "Python.Python.3.12" "Python 3.12"
Install-PackageIfMissing "node" "OpenJS.NodeJS.LTS" "Node.js LTS"

if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    Write-Host "正在安装 Codex CLI ..." -ForegroundColor Cyan
    & npm.cmd install --global '@openai/codex'
    if ($LASTEXITCODE -ne 0) { throw "Codex CLI 安装失败。" }
    Refresh-Path
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "正在创建 Python 环境 ..." -ForegroundColor Cyan
    & python -m venv .venv
}
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt

Write-Host "正在构建工作台当前界面 ..." -ForegroundColor Cyan
Push-Location "collector\workbench-command-center"
try {
    & npm.cmd install
    if ($LASTEXITCODE -ne 0) { throw "工作台依赖安装失败。" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "工作台界面构建失败。" }
} finally { Pop-Location }

Write-Host "正在构建 1688 采集插件 ..." -ForegroundColor Cyan
Push-Location "collector\edge-extension"
try {
    & npm.cmd install
    if ($LASTEXITCODE -ne 0) { throw "采集插件依赖安装失败。" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "采集插件构建失败。" }
} finally { Pop-Location }

@("logs", "runtime", "products", "batches") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot $_) | Out-Null
}

Write-Host "Windows 初始化完成。" -ForegroundColor Green
if ($SetupOnly) { exit 0 }

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:8765"
}
Write-Host "工作台地址：http://127.0.0.1:8765（按 Ctrl+C 停止）" -ForegroundColor Green
& $VenvPython -m uvicorn app:app --app-dir "collector\local-ingest" --host 127.0.0.1 --port 8765
