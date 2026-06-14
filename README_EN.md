# Venlta

A sing-box GUI client built with PySide6 and Preact.

[中文](./README.md)

### What It Does

Venlta is a desktop proxy client that wraps sing-box. It provides a graphical interface for managing proxy nodes, subscriptions, routing rules, and DNS settings. The backend generates sing-box configuration, starts and monitors the sing-box process, and exposes APIs to the frontend through Qt's QWebChannel. The frontend runs inside a QWebEngineView and communicates with the backend via a bridge object.

### Supported Protocols

VMess, VLESS, Trojan, Shadowsocks, Hysteria2, WireGuard, TUIC

### Proxy Modes

- **System Proxy** -- Sets HTTP/SOCKS proxy in the operating system. No special privileges required.
- **TUN Mode** -- Creates a virtual network interface to capture all traffic. Requires root/admin privileges. On Linux, a polkit helper is provided; on Windows, UAC elevation is used.

Both modes can be enabled simultaneously.

### Subscription Parsing

Supports auto-detection and parsing of the following formats:

- sing-box JSON
- Clash YAML
- SIP008 (Shadowsocks)
- WireGuard INI configuration
- Individual proxy links (vmess://, vless://, trojan://, ss://, hysteria2://, wg://, tuic://)

### Routing

- Rule-based routing with support for `rule_set` (remote and local)
- Built-in adblock rule set injection with jsdelivr CDN mirror
- Configurable DNS: FakeIP, domain strategy, uTLS fingerprint, DNS final outbound selection

### Other Features

- Real-time traffic chart and connection list (via Clash API)
- Node latency and bandwidth testing with concurrency control
- Encrypted storage for sensitive data (Fernet, PBKDF2-derived key)
- System tray with color-coded status icon and proxy toggle menus
- Auto-update checker for both the application and the sing-box core (via GitHub Releases)
- Chinese and English UI with automatic system language detection

### Requirements

| Dependency | Version |
|---|---|
| Python | 3.10+ |
| PySide6 | 6.5+ |
| Node.js or Bun | 18+ or 1.0+ |
| sing-box | 1.13+ |

### Running from Source

```bash
# Install Python dependencies
pip install -r requirements.txt

# Build the frontend
cd frontend
npm install
npm run build
cd ..

# Run (automatically loads frontend/dist/)
python -m backend.main
```

### Development Mode

Run with the Vite dev server for hot reload:

```bash
# Terminal 1: Frontend dev server
cd frontend && npm install && npm run dev

# Terminal 2: Backend
VENLTA_DEV=1 python -m backend.main
```

In dev mode, the backend loads `http://localhost:5173` instead of the built static files. The frontend communicates with the backend through a mock bridge that falls back to `callBridge()` when the real QWebChannel is unavailable.

### Production Build (Nuitka)

Linux and macOS:

```bash
./build.sh          # Full build (frontend + backend, standalone directory)
./build.sh frontend # Frontend only
./build.sh backend  # Backend only (requires frontend build first)
./build.sh onefile  # Single executable file
```

Windows:

```powershell
.\build.ps1          # Full build (standalone directory)
.\build.ps1 frontend # Frontend only
.\build.ps1 backend  # Backend only
.\build.ps1 onefile  # Single executable file
```

The build output goes to `build/main.dist/` (standalone) or `build/Venlta` (onefile). Nuitka may ignore `--output-filename` and produce `main.bin` / `main.exe` instead; the build scripts automatically rename these.

### CI/CD (GitHub Actions)

A workflow is provided at `.github/workflows/build.yml`. It builds on Ubuntu 22.04 (Linux) and Windows Server (Windows), producing tar.gz and zip archives respectively.

- **Push a tag** (`v*`) to trigger a build and create a GitHub Release with the artifacts attached.
- **Manual trigger** via the "Run workflow" button in the Actions tab.

### Project Structure

```
Venlta/
├── backend/
│   ├── main.py                  # Entry point: PySide6 window, module initialization
│   ├── tray.py                  # System tray icon and context menu
│   ├── bridge/
│   │   ├── venlta_bridge.py     # QWebChannel bridge: all frontend-callable APIs
│   │   ├── signals.py           # Signal definitions
│   │   └── result.py            # BridgeResult wrapper and @bridge_method decorator
│   ├── core/
│   │   ├── config_manager.py    # sing-box configuration builder (outbounds, routes, DNS)
│   │   ├── singbox_manager.py   # sing-box process lifecycle and state management
│   │   ├── database.py          # SQLite persistence with migration support
│   │   ├── subscription.py      # Subscription fetcher and multi-format parser
│   │   ├── system_proxy.py      # OS-level proxy setting (GNOME/KDE/Windows/macOS)
│   │   ├── tun_elevator.py      # TUN privilege elevation (polkit/UAC/osascript)
│   │   ├── speed_tester.py      # Bandwidth testing with concurrency control
│   │   ├── stats_collector.py   # Traffic and connection stats via Clash API
│   │   ├── auto_updater.py      # GitHub Releases update checker
│   │   └── port_detector.py     # Port conflict detection
│   └── utils/
│       ├── i18n.py              # Internationalization
│       ├── crypto.py            # Fernet encryption for sensitive data
│       ├── logger.py            # Logging setup
│       └── constants.py         # Data directory resolution
├── frontend/
│   ├── src/
│   │   ├── app.tsx              # Root component: sidebar + page router
│   │   ├── main.tsx             # Entry point, QWebChannel initialization
│   │   ├── pages/
│   │   │   ├── dashboard/       # Proxy toggle, traffic chart, connection list
│   │   │   ├── nodes/           # Node list, subscription management, speed test
│   │   │   ├── rules/           # Routing rule and rule_set management
│   │   │   ├── logs/            # Real-time log viewer
│   │   │   └── settings/        # Application settings, sing-box core management
│   │   ├── stores/              # Preact Signals stores (proxy, node, stats, log, toast)
│   │   ├── components/          # Reusable UI components
│   │   ├── lib/
│   │   │   ├── api.ts           # Bridge API wrapper with mock fallback
│   │   │   ├── qwebchannel.js   # Qt QWebChannel JavaScript binding
│   │   │   ├── format.ts        # Byte/rate formatting utilities
│   │   │   └── icons.tsx        # SVG icon components
│   │   └── i18n/
│   │       ├── en.json          # English translations
│   │       └── zh.json          # Chinese translations
│   ├── index.html
│   └── vite.config.ts
├── resources/
│   ├── icons/                   # Application icons (PNG, ICO, SVG)
│   └── polkit/                  # Linux polkit policy file for TUN elevation
├── build.sh                     # Linux/macOS build script
├── build.ps1                    # Windows build script
├── .github/workflows/build.yml  # CI/CD workflow
├── requirements.txt             # Python dependencies
├── pyproject.toml               # Project metadata
└── VERSION                      # Current version number
```

### Tray Icon States

The tray icon background color indicates the current proxy state:

| Background | Meaning |
|---|---|
| Gray | Stopped |
| Green | Running (no mode specified) |
| Blue | System Proxy enabled |
| Red-brown | TUN Mode enabled |
| Purple | Both System Proxy and TUN enabled |

### Architecture

```
┌─────────────────────────────────────────────────────┐
│                   PySide6 (Qt)                       │
│  ┌──────────────┐  ┌──────────────────────────────┐ │
│  │ SystemTray   │  │ MainWindow                    │ │
│  │              │  │  ┌──────────────────────────┐ │ │
│  │ Toggle proxy │  │  │ QWebEngineView           │ │ │
│  │ Toggle TUN   │  │  │  ┌────────────────────┐  │ │ │
│  │ Restart      │  │  │  │ Preact Frontend    │  │ │ │
│  │ Quit         │  │  │  │ (Vite-built SPA)   │  │ │ │
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
│  │Config│ │Singbox │ │System  │ │Stats     │         │
│  │Mgr   │ │Manager │ │Proxy   │ │Collector │         │
│  └──────┘ └───┬────┘ └────────┘ └──────────┘         │
│               │                                       │
│         ┌─────┴──────┐                                │
│         │ sing-box   │                                │
│         │ (subprocess)│                               │
│         └────────────┘                                │
└─────────────────────────────────────────────────────┘
```

The frontend runs inside a Chromium-based QWebEngineView. It calls backend methods through Qt's QWebChannel bridge, which exposes the `VenltaBridge` QObject as `window.bridge` in JavaScript. Signals emitted by the backend (proxy state changes, traffic updates, log lines) are delivered to the frontend as JavaScript callbacks on the bridge object.

The sing-box core runs as a child process managed by `SingboxManager`. It is started with a generated configuration file and monitored for crashes with automatic restart (up to 3 times within 60 seconds, with exponential backoff). The Clash API exposed by sing-box is used for real-time traffic statistics, connection management, and latency testing.

### License

MIT
