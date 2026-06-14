#!/usr/bin/env bash
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

    if command -v bun &>/dev/null; then
        bun install
        bun run build
    elif command -v npm &>/dev/null; then
        npm install
        npm run build
    else
        log_error "未找到 bun 或 npm"
        exit 1
    fi

    cd "$SCRIPT_DIR"
    log_info "前端构建完成"
}

# ── 后端构建（Nuitka） ────────────────────────────────────
build_backend() {
    local BUILD_MODE="${1:-standalone}"

    if [ ! -d "frontend/dist" ] || [ ! -f "frontend/dist/index.html" ]; then
        log_error "前端未构建，请先运行 ./build.sh frontend"
        exit 1
    fi

    log_info "检查 Nuitka..."
    if ! python3 -m nuitka --version &>/dev/null; then
        log_error "Nuitka 未安装：pip install nuitka ordered-set"
        exit 1
    fi

    # 静默修复 Nuitka 源码中的 patchelf 版本限制
    NUITKA_DIR=$(python3 -c "import nuitka; print(nuitka.__path__[0])" 2>/dev/null || true)
    if [ -n "$NUITKA_DIR" ]; then
        grep -rl '"0.18.0"' "$NUITKA_DIR" --include="*.py" 2>/dev/null | while read -r f; do
            if grep -q "patchelf\|Patchelf\|PATCH_ELF" "$f" 2>/dev/null; then
                sed -i 's/"0\.18\.0"/"0.99.0"/g' "$f" 2>/dev/null || true
            fi
        done || true
    fi

    VERSION=$(cat VERSION 2>/dev/null || echo "0.0.0")
    log_info "构建 Venlta v${VERSION}..."

    OS_NAME="$(uname -s)"
    EXE_NAME="Venlta"
    if [[ "$OS_NAME" == MINGW* ]] || [[ "$OS_NAME" == MSYS* ]] || [[ "$OS_NAME" == CYGWIN* ]]; then
        EXE_NAME="Venlta.exe"
    fi

    NUITKA_ARGS=()
    if [ "$BUILD_MODE" = "onefile" ]; then
        NUITKA_ARGS+=(--onefile)
    else
        NUITKA_ARGS+=(--standalone)
    fi

    NUITKA_ARGS+=(
        --enable-plugin=pyside6
        --include-data-dir=frontend/dist=frontend
        --include-data-dir=resources=resources
        --include-data-file=VERSION=VERSION
        --output-filename="$EXE_NAME"
        --output-dir=build
        --assume-yes-for-downloads
        --follow-imports
    )

    if [[ "$OS_NAME" == MINGW* ]] || [[ "$OS_NAME" == MSYS* ]] || [[ "$OS_NAME" == CYGWIN* ]]; then
        NUITKA_ARGS+=(
            --windows-icon-from-ico=resources/icons/venlta.ico
            --windows-disable-console
        )
    elif [[ "$OS_NAME" == "Linux" ]]; then
        NUITKA_ARGS+=(--linux-icon=resources/icons/venlta.png)
    fi

    log_info "执行 Nuitka 构建（$BUILD_MODE）..."
    python3 -m nuitka "${NUITKA_ARGS[@]}" backend/main.py

    # 修正文件名
    if [ "$BUILD_MODE" = "standalone" ]; then
        DIST_DIR="build/main.dist"
        for old_name in main.bin main main.exe; do
            if [ -f "$DIST_DIR/$old_name" ]; then
                mv "$DIST_DIR/$old_name" "$DIST_DIR/$EXE_NAME"
                break
            fi
        done
        chmod +x "$DIST_DIR/$EXE_NAME" 2>/dev/null || true
        log_info "构建完成：$DIST_DIR/$EXE_NAME"
    else
        for old_name in main.bin main main.exe; do
            if [ -f "build/$old_name" ]; then
                mv "build/$old_name" "build/$EXE_NAME"
                break
            fi
        done
        chmod +x "build/$EXE_NAME" 2>/dev/null || true
        log_info "构建完成：build/$EXE_NAME"
    fi
}

case "${1:-all}" in
    frontend) build_frontend ;;
    backend)  build_backend standalone ;;
    onefile)  build_frontend; build_backend onefile ;;
    all)      build_frontend; build_backend standalone ;;
    *)        echo "用法: $0 [frontend|backend|all|onefile]"; exit 1 ;;
esac

log_info "✅ 构建完成！"
