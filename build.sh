#!/usr/bin/env bash
# Venlta 构建脚本
# 用法：
#   ./build.sh           — 完整构建（前端 + 后端）
#   ./build.sh frontend  — 仅构建前端
#   ./build.sh backend   — 仅构建后端（需先构建前端）

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── 前端构建 ──────────────────────────────────────────────
build_frontend() {
    log_info "构建前端..."
    cd frontend

    # 检查 node/bun
    if command -v bun &>/dev/null; then
        log_info "使用 bun 安装依赖..."
        bun install
        log_info "使用 bun 构建..."
        bun run build
    elif command -v npm &>/dev/null; then
        log_info "使用 npm 安装依赖..."
        npm install
        log_info "使用 npm 构建..."
        npm run build
    else
        log_error "未找到 bun 或 npm，请先安装 Node.js"
        exit 1
    fi

    cd "$SCRIPT_DIR"
    log_info "前端构建完成：frontend/dist/"
}

# ── 后端构建（Nuitka） ────────────────────────────────────
build_backend() {
    # 检查前端构建产物
    if [ ! -d "frontend/dist" ] || [ ! -f "frontend/dist/index.html" ]; then
        log_error "前端构建产物不存在，请先运行 ./build.sh frontend"
        exit 1
    fi

    log_info "检查 Nuitka..."
    if ! command -v python -m nuitka &>/dev/null; then
        log_error "Nuitka 未安装。请运行：pip install nuitka ordered-set"
        exit 1
    fi

    # 读取版本号
    VERSION=$(cat VERSION 2>/dev/null || echo "0.0.0")
    log_info "构建 Venlta v${VERSION}..."

    # Nuitka 构建参数
    NUITKA_ARGS=(
        --standalone
        --enable-plugin=pyside6
        --include-data-dir=frontend/dist=frontend
        --include-data-dir=backend/resources=resources
        --include-data-file=VERSION=VERSION
        --output-filename=venlta
        --output-dir=build
        --assume-yes-for-downloads
        --follow-imports
    )

    # 平台特定参数
    if [ "$(uname -s)" = "Windows" ]; then
        NUITKA_ARGS+=(
            --windows-icon-from-ico=resources/icons/venlta.ico
            --windows-disable-console
        )
    elif [ "$(uname -s)" = "Linux" ]; then
        NUITKA_ARGS+=(
            --linux-icon=resources/icons/venlta.png
        )
    fi

    log_info "执行 Nuitka 构建..."
    python -m nuitka "${NUITA_ARGS[@]}" backend/main.py

    log_info "后端构建完成：build/main.dist/"
}

# ── 主流程 ────────────────────────────────────────────────
case "${1:-all}" in
    frontend)
        build_frontend
        ;;
    backend)
        build_backend
        ;;
    all)
        build_frontend
        build_backend
        ;;
    *)
        echo "用法: $0 [frontend|backend|all]"
        exit 1
        ;;
esac

log_info "✅ 构建完成！"
