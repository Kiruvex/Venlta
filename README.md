# Venlta

基于 sing-box 的桌面代理客户端，使用 PySide6 + Preact 构建。

[English](./README_EN.md)

### 这是什么

Venlta 是一个基于 sing-box 的桌面代理客户端，提供图形界面来管理代理节点、订阅、路由规则和 DNS 设置。后端负责生成 sing-box 配置、启停和监控 sing-box 进程，并通过 Qt 的 QWebChannel 将 API 暴露给前端。前端运行在 QWebEngineView 中，通过桥接对象与后端通信。

### 支持的协议

VMess、VLESS、Trojan、Shadowsocks、Hysteria2、WireGuard、TUIC

### 代理模式

- **系统代理** -- 在操作系统中设置 HTTP/SOCKS 代理，无需特殊权限。
- **TUN 模式** -- 创建虚拟网卡捕获所有流量，需要 root/管理员权限。Linux 提供 polkit 辅助程序，Windows 使用 UAC 提权。

两种模式可以同时开启。

### 订阅解析

自动检测并解析以下格式：

- sing-box JSON
- Clash YAML
- SIP008（Shadowsocks）
- WireGuard INI 配置
- 单个代理链接（vmess://、vless://、trojan://、ss://、hysteria2://、wg://、tuic://）

### 路由

- 基于规则的路由，支持 `rule_set`（远程和本地）
- 内置广告拦截规则集注入，使用 jsdelivr CDN 镜像
- 可配置 DNS：FakeIP、域名策略、uTLS 指纹、DNS 最终出站选择

### 其他功能

- 实时流量图表和连接列表（通过 Clash API）
- 节点延迟和带宽测试，带并发控制
- 敏感数据加密存储（Fernet，PBKDF2 派生密钥）
- 系统托盘，带颜色状态图标和代理切换菜单
- 自动更新检查，支持应用和 sing-box 核心（通过 GitHub Releases）
- 中英文界面，自动检测系统语言

### 环境要求

| 依赖 | 版本 |
|---|---|
| Python | 3.10+ |
| PySide6 | 6.5+ |
| Node.js 或 Bun | 18+ 或 1.0+ |
| sing-box | 1.13+ |

### 从源码运行

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 构建前端
cd frontend
npm install
npm run build
cd ..

# 运行（自动加载 frontend/dist/）
python -m backend.main
```

### 开发模式

使用 Vite 开发服务器进行热更新：

```bash
# 终端 1：前端开发服务器
cd frontend && npm install && npm run dev

# 终端 2：后端
VENLTA_DEV=1 python -m backend.main
```

开发模式下，后端加载 `http://localhost:5173` 而非构建后的静态文件。前端通过模拟桥接与后端通信，当真实 QWebChannel 不可用时回退到 `callBridge()`。

### 生产构建（Nuitka）

Linux 和 macOS：

```bash
./build.sh          # 完整构建（前端 + 后端，standalone 目录）
./build.sh frontend # 仅前端
./build.sh backend  # 仅后端（需先构建前端）
./build.sh onefile  # 单文件可执行
```

Windows：

```powershell
.\build.ps1          # 完整构建（standalone 目录）
.\build.ps1 frontend # 仅前端
.\build.ps1 backend  # 仅后端
.\build.ps1 onefile  # 单文件可执行
```

构建产物位于 `build/main.dist/`（standalone）或 `build/Venlta`（onefile）。Nuitka 可能忽略 `--output-filename` 参数而产生 `main.bin` / `main.exe`，构建脚本会自动重命名。

### CI/CD（GitHub Actions）

提供了 `.github/workflows/build.yml` 工作流。在 Ubuntu 22.04（Linux）和 Windows Server（Windows）上构建，分别生成 tar.gz 和 zip 压缩包。

- **推送标签**（`v*`）触发构建并创建 GitHub Release，附带构建产物。
- **手动触发**：在 Actions 标签页点击 "Run workflow" 按钮。

### 项目结构

```
Venlta/
├── backend/
│   ├── main.py                  # 入口：PySide6 窗口、模块初始化
│   ├── tray.py                  # 系统托盘图标和右键菜单
│   ├── bridge/
│   │   ├── venlta_bridge.py     # QWebChannel 桥接：所有前端可调用的 API
│   │   ├── signals.py           # 信号定义
│   │   └── result.py            # BridgeResult 包装器和 @bridge_method 装饰器
│   ├── core/
│   │   ├── config_manager.py    # sing-box 配置构建器（出站、路由、DNS）
│   │   ├── singbox_manager.py   # sing-box 进程生命周期和状态管理
│   │   ├── database.py          # SQLite 持久化，带迁移支持
│   │   ├── subscription.py      # 订阅获取和多格式解析
│   │   ├── system_proxy.py      # 操作系统代理设置（GNOME/KDE/Windows/macOS）
│   │   ├── tun_elevator.py      # TUN 提权（polkit/UAC/osascript）
│   │   ├── speed_tester.py      # 带宽测试，带并发控制
│   │   ├── stats_collector.py   # 通过 Clash API 采集流量和连接统计
│   │   ├── auto_updater.py      # GitHub Releases 更新检查
│   │   └── port_detector.py     # 端口冲突检测
│   └── utils/
│       ├── i18n.py              # 国际化
│       ├── crypto.py            # Fernet 加密敏感数据
│       ├── logger.py            # 日志配置
│       └── constants.py         # 数据目录解析
├── frontend/
│   ├── src/
│   │   ├── app.tsx              # 根组件：侧边栏 + 页面路由
│   │   ├── main.tsx             # 入口点，QWebChannel 初始化
│   │   ├── pages/
│   │   │   ├── dashboard/       # 代理开关、流量图表、连接列表
│   │   │   ├── nodes/           # 节点列表、订阅管理、速度测试
│   │   │   ├── rules/           # 路由规则和 rule_set 管理
│   │   │   ├── logs/            # 实时日志查看
│   │   │   └── settings/        # 应用设置、sing-box 核心管理
│   │   ├── stores/              # Preact Signals 状态（proxy, node, stats, log, toast）
│   │   ├── components/          # 可复用 UI 组件
│   │   ├── lib/
│   │   │   ├── api.ts           # 桥接 API 封装，带模拟回退
│   │   │   ├── qwebchannel.js   # Qt QWebChannel JavaScript 绑定
│   │   │   ├── format.ts        # 字节/速率格式化工具
│   │   │   └── icons.tsx        # SVG 图标组件
│   │   └── i18n/
│   │       ├── en.json          # 英文翻译
│   │       └── zh.json          # 中文翻译
│   ├── index.html
│   └── vite.config.ts
├── resources/
│   ├── icons/                   # 应用图标（PNG、ICO、SVG）
│   └── polkit/                  # Linux TUN 提权的 polkit 策略文件
├── build.sh                     # Linux/macOS 构建脚本
├── build.ps1                    # Windows 构建脚本
├── .github/workflows/build.yml  # CI/CD 工作流
├── requirements.txt             # Python 依赖
├── pyproject.toml               # 项目元数据
└── VERSION                      # 当前版本号
```

### 托盘图标状态

托盘图标的背景色表示当前代理状态：

| 背景色 | 含义 |
|---|---|
| 灰色 | 已停止 |
| 绿色 | 运行中（无特定模式） |
| 蓝色 | 系统代理已开启 |
| 红棕色 | TUN 模式已开启 |
| 紫色 | 系统代理和 TUN 同时开启 |

### 架构

```
┌─────────────────────────────────────────────────────┐
│                   PySide6 (Qt)                       │
│  ┌──────────────┐  ┌──────────────────────────────┐ │
│  │ SystemTray   │  │ MainWindow                    │ │
│  │              │  │  ┌──────────────────────────┐ │ │
│  │ 切换代理     │  │  │ QWebEngineView           │ │ │
│  │ 切换 TUN     │  │  │  ┌────────────────────┐  │ │ │
│  │ 重启代理     │  │  │  │ Preact 前端        │  │ │ │
│  │ 退出         │  │  │  │ (Vite 构建的 SPA)  │  │ │ │
│  └──────┬───────┘  │  │  └────────┬───────────┘  │ │ │
│         │          │  └───────────┼───────────────┘ │ │
│         │          └──────────────┼──────────────────┘ │
│         │                         │ QWebChannel        │
│  ┌──────┴─────────────────────────┴──────────────────┐ │
│  │              VenltaBridge (QObject)                │ │
│  │  toggleSystemProxy / toggleTun / startProxy ...   │ │
│  └──┬──────────┬──────────┬──────────┬───────────────┘ │
│     │          │          │          │                  │
│  ┌──┴───┐ ┌───┴────┐ ┌───┴────┐ ┌───┴──────┐         │
│  │配置  │ │sing-box│ │系统    │ │统计      │         │
│  │管理器│ │管理器  │ │代理    │ │采集器    │         │
│  └──────┘ └───┬────┘ └────────┘ └──────────┘         │
│               │                                       │
│         ┌─────┴──────┐                                │
│         │ sing-box   │                                │
│         │ (子进程)   │                                │
│         └────────────┘                                │
└─────────────────────────────────────────────────────┘
```

前端运行在基于 Chromium 的 QWebEngineView 中，通过 Qt 的 QWebChannel 桥接调用后端方法。QWebChannel 将 `VenltaBridge` QObject 暴露为 JavaScript 中的 `window.bridge` 对象。后端发出的信号（代理状态变更、流量更新、日志行）通过桥接对象上的 JavaScript 回调传递到前端。

sing-box 核心作为子进程由 `SingboxManager` 管理，使用生成的配置文件启动。进程崩溃时会自动重启（60 秒内最多 3 次，指数退避）。sing-box 暴露的 Clash API 用于实时流量统计、连接管理和延迟测试。

### 许可证

MIT
