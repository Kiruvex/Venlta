# Venlta 构建脚本 (PowerShell / Windows)
# 用法：
#   .\build.ps1           — 完整构建（前端 + 后端）
#   .\build.ps1 frontend  — 仅构建前端
#   .\build.ps1 backend   — 仅构建后端（需先构建前端）

param(
    [string]$Target = "all"
)

$ErrorActionPreference = "Stop"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $SCRIPT_DIR

# ── 日志函数 ──────────────────────────────────────────────
function Write-Info  { Write-Host "[INFO] $args" -ForegroundColor Green }
function Write-Warn  { Write-Host "[WARN] $args" -ForegroundColor Yellow }
function Write-Err   { Write-Host "[ERROR] $args" -ForegroundColor Red }

# ── 前端构建 ──────────────────────────────────────────────
function Build-Frontend {
    Write-Info "构建前端..."
    Set-Location "$SCRIPT_DIR\frontend"

    # 检查 bun / npm
    if (Get-Command bun -ErrorAction SilentlyContinue) {
        Write-Info "使用 bun 安装依赖..."
        bun install
        Write-Info "使用 bun 构建..."
        bun run build
    }
    elseif (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Info "使用 npm 安装依赖..."
        npm install
        Write-Info "使用 npm 构建..."
        npm run build
    }
    else {
        Write-Err "未找到 bun 或 npm，请先安装 Node.js"
        exit 1
    }

    Set-Location $SCRIPT_DIR
    Write-Info "前端构建完成：frontend\dist\"
}

# ── 后端构建（Nuitka） ────────────────────────────────────
function Build-Backend {
    # 检查前端构建产物
    if (-not (Test-Path "frontend\dist\index.html")) {
        Write-Err "前端构建产物不存在，请先运行 .\build.ps1 frontend"
        exit 1
    }

    Write-Info "检查 Nuitka..."
    $nuitkaCheck = python -m nuitka --version 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Nuitka 未安装。请运行：pip install nuitka ordered-set"
        exit 1
    }

    # 读取版本号
    $VERSION = if (Test-Path "VERSION") { Get-Content VERSION -Raw } else { "0.0.0" }
    $VERSION = $VERSION.Trim()
    Write-Info "构建 Venlta v$VERSION..."

    # Nuitka 构建参数
    $nuitkaArgs = @(
        "--standalone",
        "--enable-plugin=pyside6",
        "--include-data-dir=frontend\dist=frontend",
        "--include-data-dir=backend\resources=resources",
        "--include-data-file=VERSION=VERSION",
        "--output-filename=venlta.exe",
        "--output-dir=build",
        "--assume-yes-for-downloads",
        "--follow-imports",
        # Windows 特定参数
        "--windows-icon-from-ico=resources\icons\venlta.ico",
        "--windows-disable-console"
    )

    Write-Info "执行 Nuitka 构建..."
    python -m nuitka @nuitkaArgs backend\main.py

    if ($LASTEXITCODE -eq 0) {
        Write-Info "后端构建完成：build\main.dist\"
    }
    else {
        Write-Err "Nuitka 构建失败（退出码 $LASTEXITCODE）"
        exit $LASTEXITCODE
    }
}

# ── 主流程 ────────────────────────────────────────────────
switch ($Target) {
    "frontend" { Build-Frontend }
    "backend"  { Build-Backend }
    "all"      { Build-Frontend; Build-Backend }
    default    {
        Write-Err "用法: .\build.ps1 [frontend|backend|all]"
        exit 1
    }
}

Write-Info "构建完成！"
