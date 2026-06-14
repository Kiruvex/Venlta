# Venlta 构建脚本 (PowerShell / Windows)
# 用法：
#   .\build.ps1           — 完整构建（前端 + 后端 standalone 目录）
#   .\build.ps1 frontend  — 仅构建前端
#   .\build.ps1 backend   — 仅构建后端（需先构建前端）
#   .\build.ps1 onefile   — 完整构建（单文件可执行）
#   .\build.ps1 all       — 同 .\build.ps1（standalone 目录）

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
    param([string]$BuildMode = "standalone")

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

    $EXE_NAME = "Venlta.exe"

    # Nuitka 构建参数
    $nuitkaArgs = @()

    if ($BuildMode -eq "onefile") {
        $nuitkaArgs += "--onefile"
    } else {
        $nuitkaArgs += "--standalone"
    }

    $nuitkaArgs += @(
        "--enable-plugin=pyside6",
        "--include-data-dir=frontend\dist=frontend",
        "--include-data-dir=resources=resources",
        "--include-data-file=VERSION=VERSION",
        "--output-filename=$EXE_NAME",
        "--output-dir=build",
        "--assume-yes-for-downloads",
        "--follow-imports",
        # Windows 特定参数
        "--windows-icon-from-ico=resources\icons\venlta.ico",
        "--windows-disable-console"
    )

    Write-Info "执行 Nuitka 构建（模式: $BuildMode）..."
    python -m nuitka @nuitkaArgs backend\main.py

    if ($LASTEXITCODE -ne 0) {
        Write-Err "Nuitka 构建失败（退出码 $LASTEXITCODE）"
        exit $LASTEXITCODE
    }

    # 构建后修正文件名（Nuitka 可能忽略 --output-filename 生成 main.exe）
    if ($BuildMode -eq "standalone") {
        $DIST_DIR = "build\main.dist"
        if (Test-Path "$DIST_DIR\main.exe") {
            Move-Item -Force "$DIST_DIR\main.exe" "$DIST_DIR\$EXE_NAME"
            Write-Info "重命名 main.exe → $EXE_NAME"
        }
        Write-Info "后端构建完成：$DIST_DIR\$EXE_NAME"
    } else {
        if (Test-Path "build\main.exe") {
            Move-Item -Force "build\main.exe" "build\$EXE_NAME"
            Write-Info "重命名 main.exe → $EXE_NAME"
        }
        Write-Info "后端构建完成：build\$EXE_NAME（单文件）"
    }
}

# ── 主流程 ────────────────────────────────────────────────
switch ($Target) {
    "frontend" { Build-Frontend }
    "backend"  { Build-Backend -BuildMode "standalone" }
    "onefile"  { Build-Frontend; Build-Backend -BuildMode "onefile" }
    "all"      { Build-Frontend; Build-Backend -BuildMode "standalone" }
    default    {
        Write-Err "用法: .\build.ps1 [frontend|backend|all|onefile]"
        exit 1
    }
}

Write-Info "✅ 构建完成！"
