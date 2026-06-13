# Venlta

**A modern sing-box GUI client** built with PySide6 + Preact.

[中文](#中文) | [English](#english)

---

## English

### Features

- 🛡️ **sing-box Powered** — Full sing-box 1.13+ config generation with validation & rollback
- 🌐 **Dual Proxy Modes** — System Proxy and TUN mode, independently toggleable
- 📡 **7 Protocols** — VMess, VLESS, Trojan, Shadowsocks, Hysteria2, WireGuard, TUIC
- 📋 **Subscription Management** — Auto-parsing: sing-box JSON, Clash YAML, SIP008, WireGuard config, proxy links
- 🔀 **Flexible Routing** — Rule-based routing with rule_set (remote/local), adblock injection, jsdelivr CDN mirror
- 🔍 **DNS Control** — FakeIP support, configurable domain strategy, uTLS fingerprint, DNS final outbound
- 📊 **Real-time Stats** — Traffic chart (uPlot), connection management, speed test
- 🌍 **i18n** — English & Chinese UI with auto language detection
- 🔄 **Auto Update** — GitHub Releases based auto-update for both app and sing-box core
- 🖥️ **System Tray** — Background running, proxy mode toggle, status icon with color-coded background
- 🔐 **Encrypted Storage** — Fernet (PBKDF2) encrypted sensitive data

### Requirements

| Dependency | Version |
|---|---|
| Python | 3.10+ |
| PySide6 | 6.5+ |
| Node.js / Bun | 18+ / 1.0+ |
| sing-box | 1.13+ |

### Quick Start

```bash
# 1. Install Python dependencies
pip install PySide6

# 2. Build frontend
cd frontend
npm install
npm run build
cd ..

# 3. Run (loads frontend/dist/ automatically)
python -m backend.main
```

### Development Mode

Run with Vite dev server (hot reload):

```bash
# Terminal 1: Frontend dev server
cd frontend && npm install && npm run dev

# Terminal 2: Backend with dev mode flag
VENLTA_DEV=1 python -m backend.main
```

### Production Build (Nuitka)

```bash
# Full build (frontend + backend)
./build.sh

# Frontend only
./build.sh frontend

# Backend only (requires frontend build first)
./build.sh backend
```

**Windows:**
```powershell
.\build.ps1
```

### Project Structure

```
Venlta/
├── backend/
│   ├── main.py              # Entry point (PySide6 window)
│   ├── tray.py              # System tray with color-coded status
│   ├── bridge/
│   │   └── venlta_bridge.py # QWebChannel bridge (backend API)
│   ├── core/
│   │   ├── config_manager.py    # sing-box config builder
│   │   ├── singbox_manager.py   # sing-box process manager
│   │   ├── database.py          # SQLite + migrations
│   │   ├── subscription.py      # Subscription fetcher & parser
│   │   ├── system_proxy.py      # OS proxy settings
│   │   ├── tun_elevator.py      # TUN privilege elevation
│   │   ├── speed_tester.py      # Speed & latency testing
│   │   ├── stats_collector.py   # Clash API stats
│   │   ├── auto_updater.py      # Auto-update checker
│   │   └── port_detector.py     # Port conflict detection
│   └── utils/
│       ├── i18n.py          # Internationalization
│       ├── crypto.py        # Fernet encryption
│       └── logger.py        # Logging setup
├── frontend/
│   ├── src/
│   │   ├── app.tsx          # Root component
│   │   ├── main.tsx         # Entry + QWebChannel patch
│   │   ├── pages/           # Dashboard, Nodes, Rules, Logs, Settings
│   │   ├── stores/          # Preact Signals stores
│   │   ├── lib/
│   │   │   ├── api.ts       # Bridge API + mock bridge
│   │   │   └── qwebchannel.js
│   │   └── i18n/            # en.json, zh.json
│   ├── index.html
│   └── vite.config.ts
├── resources/
│   └── icons/               # App icons (PNG + ICO)
├── build.sh                 # Linux/macOS build script
├── build.ps1                # Windows build script
└── VERSION                  # Version number
```

### Tray Icon Status

The system tray icon uses **background color** to indicate proxy status:

| Color | Status |
|---|---|
| 🔘 Gray | Stopped |
| 🟢 Green | Running (no specific mode) |
| 🔵 Blue | System Proxy |
| 🟤 Red-brown | TUN Mode |
| 🟣 Purple | System Proxy + TUN |

### License

MIT

---

## 中文

### 功能特性

- 🛡️ **sing-box 驱动** — 完整的 sing-box 1.13+ 配置生成，含验证与回滚
- 🌐 **双代理模式** — 系统代理与 TUN 模式独立控制，可同时开启
- 📡 **7 种协议** — VMess、VLESS、Trojan、Shadowsocks、Hysteria2、WireGuard、TUIC
- 📋 **订阅管理** — 自动解析：sing-box JSON、Clash YAML、SIP008、WireGuard 配置、代理链接
- 🔀 **灵活路由** — 基于规则的路由，支持 rule_set（远程/本地）、广告拦截注入、jsdelivr CDN 镜像
- 🔍 **DNS 控制** — FakeIP 支持、可配置域名策略、uTLS 指纹、DNS 最终出站选择
- 📊 **实时统计** — 流量图表（uPlot）、连接管理、速度测试
- 🌍 **国际化** — 中英文界面，自动语言检测
- 🔄 **自动更新** — 基于 GitHub Releases 的应用和 sing-box 核心自动更新
- 🖥️ **系统托盘** — 后台运行、代理模式切换、状态背景色图标
- 🔐 **加密存储** — Fernet (PBKDF2) 加密敏感数据

### 环境要求

| 依赖 | 版本 |
|---|---|
| Python | 3.10+ |
| PySide6 | 6.5+ |
| Node.js / Bun | 18+ / 1.0+ |
| sing-box | 1.13+ |

### 快速开始

```bash
# 1. 安装 Python 依赖
pip install PySide6

# 2. 构建前端
cd frontend
npm install
npm run build
cd ..

# 3. 运行（自动加载 frontend/dist/）
python -m backend.main
```

### 开发模式

使用 Vite 开发服务器（热更新）：

```bash
# 终端 1：前端开发服务器
cd frontend && npm install && npm run dev

# 终端 2：后端开发模式
VENLTA_DEV=1 python -m backend.main
```

### 生产构建（Nuitka）

```bash
# 完整构建（前端 + 后端）
./build.sh

# 仅构建前端
./build.sh frontend

# 仅构建后端（需先构建前端）
./build.sh backend
```

**Windows：**
```powershell
.\build.ps1
```

### 项目结构

```
Venlta/
├── backend/
│   ├── main.py              # 入口（PySide6 窗口）
│   ├── tray.py              # 系统托盘（背景色状态图标）
│   ├── bridge/
│   │   └── venlta_bridge.py # QWebChannel 桥接（后端 API）
│   ├── core/
│   │   ├── config_manager.py    # sing-box 配置构建器
│   │   ├── singbox_manager.py   # sing-box 进程管理
│   │   ├── database.py          # SQLite + 迁移
│   │   ├── subscription.py      # 订阅获取与解析
│   │   ├── system_proxy.py      # 系统代理设置
│   │   ├── tun_elevator.py      # TUN 提权
│   │   ├── speed_tester.py      # 速度与延迟测试
│   │   ├── stats_collector.py   # Clash API 统计
│   │   ├── auto_updater.py      # 自动更新检查
│   │   └── port_detector.py     # 端口冲突检测
│   └── utils/
│       ├── i18n.py          # 国际化
│       ├── crypto.py        # Fernet 加密
│       └── logger.py        # 日志配置
├── frontend/
│   ├── src/
│   │   ├── app.tsx          # 根组件
│   │   ├── main.tsx         # 入口 + QWebChannel 补丁
│   │   ├── pages/           # 仪表盘、节点、规则、日志、设置
│   │   ├── stores/          # Preact Signals 状态
│   │   ├── lib/
│   │   │   ├── api.ts       # 桥接 API + 模拟桥接
│   │   │   └── qwebchannel.js
│   │   └── i18n/            # en.json, zh.json
│   ├── index.html
│   └── vite.config.ts
├── resources/
│   └── icons/               # 应用图标（PNG + ICO）
├── build.sh                 # Linux/macOS 构建脚本
├── build.ps1                # Windows 构建脚本
└── VERSION                  # 版本号
```

### 托盘图标状态

系统托盘图标使用**背景色**指示代理状态：

| 颜色 | 状态 |
|---|---|
| 🔘 灰色 | 已停止 |
| 🟢 绿色 | 运行中（无特定模式） |
| 🔵 蓝色 | 系统代理 |
| 🟤 红棕色 | TUN 模式 |
| 🟣 紫色 | 系统代理 + TUN |

### 许可证

MIT
