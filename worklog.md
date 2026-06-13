# Venlta Worklog

---
Task ID: 1
Agent: Main
Task: Redesign dashboard toggle cards with proper state-aware backgrounds

Work Log:
- Analyzed previous session's work: TUN and system proxy were made independent in backend
- Redesigned dashboard: 2 independent toggle cards (System Proxy + TUN)
- Added state-aware backgrounds: ON=vibrant gradient, OFF=white/dark card with identity tint
- Added iOS-style toggle switches, responsive grid, i18n keys

Stage Summary:
- Dashboard has 2 independent toggle cards with proper backgrounds
- Each card has distinct visual identity even when OFF

---
Task ID: 2
Agent: Main
Task: Fix button logic and proxy/TUN off logic to match NekoBox model

Work Log:
- Analyzed NekoBox source code for spmode_vpn / spmode_system_proxy behavior
- Found key issues in Venlta's logic:
  1. toggleSystemProxy(true) when sing-box NOT running → only saved setting, didn't start sing-box → system proxy not actually applied but UI showed "on"
  2. toggleTun(true) when sing-box NOT running → only saved setting, didn't start sing-box → TUN not actually active but UI showed "on"
  3. Tray toggle → start/stopped sing-box directly instead of toggling system proxy
  4. Disabling system proxy or TUN when the other is also off → sing-box kept running doing nothing
- Fixed toggleSystemProxy():
  - Enabling + sing-box not running → start sing-box first (with port conflict check)
  - Disabling + TUN also off → stop sing-box (no mode active)
- Fixed toggleTun():
  - Enabling + sing-box not running → start sing-box (TUN config already written)
  - Disabling + system proxy still on → restart sing-box (remove TUN, keep mixed inbound)
  - Disabling + system proxy also off → stop sing-box (no mode active)
- Fixed tray toggle (_on_tray_toggle_proxy):
  - Now toggles system proxy mode (NekoBox toggle_system_proxy model)
  - No longer directly starts/stops sing-box
  - Follows same lifecycle: enable→start if needed, disable→stop if both off
- Fixed auto-start logic (_startup_auto_start):
  - Only starts sing-box if at least one mode is enabled (system proxy or TUN)
  - Skips auto-start if both modes are off
- Updated fix.md with #26 documenting the independence fix

Stage Summary:
- NekoBox-model lifecycle: any mode on → sing-box runs, both off → sing-box stops
- All toggle actions (dashboard, tray) properly manage sing-box lifecycle
- No more UI/backend state inconsistency when toggling modes

---
Task ID: 3
Agent: Main
Task: Fix delayed-operation buttons not disabling during operation (speed test, latency test, subscription refresh)

Work Log:
- Identified root cause: backend's testSpeed/testLatency are fire-and-forget (return immediately, results via Qt signals)
- Frontend loading states cleared in `finally` block when callBridge returned, not when actual work finished
- Moved loading states from component-local signals to global nodeStore signals
- Added pending count tracking in nodeStore:
  - `startLatencyTest(N, isAll)` → sets loading + pending count
  - `startSpeedTest(N)` → sets loading + pending count
  - `startSubUpdate(subId)` → sets updatingSubId
- Signal handlers in app.tsx now track completion:
  - `updateLatencyResults()` → decrements `_pendingLatencyCount`, clears loading when 0
  - `updateSpeedResults()` → decrements `_pendingSpeedCount`, clears loading when 0
  - `handleSubUpdated()` → calls `finishSubUpdate()` to clear loading
- Added safety timeouts (60s latency, 120s speed, 30s subscription) to prevent permanent stuck states
- Added `forceFinishLatencyTest()` and `forceFinishSpeedTest()` for bridge call failure cases
- Removed old local `isTestingLatency`, `isTestingAllLatency`, `isTestingSpeed`, `updatingSubId` signals from nodes/index.tsx
- All button disabled/text states now reference `nodeStore.*` global signals

Stage Summary:
- Speed test button stays disabled during entire test (N nodes × 10s each)
- Latency test buttons stay disabled until all batch results arrive
- Subscription refresh button stays disabled until subscriptionUpdated signal
- All buttons re-enable correctly when operations complete (verified via signal-based tracking)

---
Task ID: 4
Agent: Main
Task: Fix all tray issues by comparing with NekoBox implementation

Work Log:
- Compared NekoBox vs Venlta tray implementation in detail
- Identified 8 issues: 2 critical bugs + 6 missing features
- Rewrote tray.py completely (NekoBox-style):
  - Added `menu_spmode` submenu with independent System Proxy / TUN checkboxes + "Disable All" action
  - Added "Restart Proxy" menu item
  - Added state badge drawing on tray icon (green=running, blue=system proxy, brown=TUN, purple=both)
  - Fixed Windows icon never changing (removed .ico early-return, use PNG+badge for all platforms)
  - Added Trigger (single-click) response to toggle window visibility
  - Added `aboutToShow` dynamic menu rebuild to refresh checkbox states
  - Enhanced tooltip to show current mode (e.g. "Venlta - 系统代理 + TUN")
  - New signals: `toggle_system_proxy_requested(bool)`, `toggle_tun_requested(bool)`, `restart_proxy_requested()`
- Fixed main.py:
  - Replaced `_on_tray_toggle_proxy()` with `_on_tray_toggle_system_proxy()` and `_on_tray_toggle_tun()`
  - Both now call bridge methods (`toggleSystemProxy()`, `toggleTun()`) — no more duplicate logic, mutex lock is properly acquired
  - Added `_on_tray_restart_proxy()` calling `bridge.restartProxy()`
  - Updated `on_proxy_state_changed` to pass `system_proxy_enabled` and `tun_enabled` to `tray.set_proxy_state()`
  - Removed old `toggle_proxy_requested` signal connection
- Updated i18n with new keys: `tray.proxy_mode`, `tray.system_proxy`, `tray.tun_mode`, `tray.disable_all`, `tray.restart_proxy`, `tray.mode_system_proxy`, `tray.mode_tun`

Stage Summary:
- 🔴 Fixed: Windows tray icon now changes based on proxy state (removed .ico early-return)
- 🔴 Fixed: Tray toggle now uses bridge methods with mutex lock — no race conditions
- 🟡 Added: TUN toggle in tray menu (NekoBox menu_spmode style)
- 🟡 Added: Restart Proxy menu item
- 🟡 Added: State badges on tray icon (colored corner indicator)
- 🟡 Added: Rich tooltip showing current proxy mode
- 🟡 Added: Single-click (Trigger) toggles window visibility
- 🟡 Added: Dynamic menu rebuild on aboutToShow
- Safety timeouts prevent permanent stuck states

---
Task ID: 5
Agent: Main
Task: Fix sing-box config validation failure: store_dns unknown field + ensure config aligns with official docs

Work Log:
- Read official sing-box documentation from https://sing-box.sagernet.org/zh/configuration/ and https://sing-box.sagernet.org/zh/manual/proxy/client/
- Fetched and parsed docs for: route, cache_file, TUN inbound, DNS, DNS server, DNS rule, route rule, client proxy examples
- Identified root cause: `store_dns` field in `experimental.cache_file` is only available since sing-box 1.14.0, but user's version is 1.13.x
- Error: `FATAL[0000] decode config: experimental.cache_file.store_dns: json: unknown field "store_dns"`
- Previous session incorrectly replaced `store_rdrc` with `store_dns`, breaking both TUN and system proxy
- Fixed: Replaced `store_dns: True` with `store_rdrc: True` (available since 1.9.0, deprecated but still works in 1.14.0+)
- Cross-verified entire config against official docs:
  - ✅ Route rules: sniff → hijack-dns → ip_is_private → user rules (matches official pattern)
  - ✅ auto_detect_interface: True in route
  - ✅ default_domain_resolver: "dns-direct" (equivalent to official's "local")
  - ✅ TUN address: 172.19.0.1/30 (matches official)
  - ✅ DNS server format: type + server fields (1.12+ format)
  - ✅ DNS rules: action field (1.11+ format)
  - ✅ cache_file: store_rdrc instead of store_dns

Stage Summary:
- Fixed FATAL validation error caused by store_dns (1.14.0-only field)
- Config fully aligned with official sing-box documentation patterns

---
Task ID: 6
Agent: Main
Task: Fix TUN mode - DNS routing loop prevention + explicit stack setting

Work Log:
- Analyzed user's latest log: config validates OK, sing-box starts in TUN mode, but no traffic flows
- Root cause analysis: DNS server connections may be captured by TUN device, creating routing loop
  - Without `detour` on DNS servers, DNS module's connection to 8.8.8.8 could go through TUN → sing-box → DNS module → 8.8.8.8 → TUN → infinite loop!
  - This explains why BOTH domestic AND international sites don't work (DNS completely broken)
- Verified official sing-box docs: new DNS server format supports `detour` field (part of Dial Fields)
- Fixed: Added `detour: "proxy"` to dns-remote (NekoBox pattern: DNS via proxy, bypasses TUN)
- Fixed: Added `detour: "direct"` to dns-direct (NekoBox pattern: DNS direct, bypasses TUN)
- Fixed: Always explicitly set TUN stack field (was skipped when stack="gvisor", could use wrong default)
- These 3 fixes align with NekoBox's ConfigBuilder.cpp approach

Stage Summary:
- DNS routing loop prevention: detour fields ensure DNS connections bypass TUN device
- Explicit stack setting ensures consistent behavior across sing-box versions
- store_rdrc compatibility fix from previous task maintained

---
Task ID: 6
Agent: build-script-fixer
Task: Fix build script Windows support and create .ico file

Work Log:
- Created build.ps1 for Windows builds (PowerShell equivalent of build.sh)
- Generated venlta.ico (28KB, 6 sizes: 16-256px) from venlta.png using Pillow
- Fixed build.sh platform detection: replaced `if [ "$(uname -s)" = "Windows" ]` with `case` statement detecting MINGW*/MSYS*/CYGWIN*
- Fixed output filename on Windows: venlta.exe instead of venlta
- Added Darwin case (macOS, no extra icon flags needed)

Stage Summary:
- Windows build now supported via build.ps1 (standalone PowerShell script)
- venlta.ico icon file created for Windows builds (multi-size ICO from 512px PNG)
- build.sh correctly detects Windows via MINGW/MSYS/CYGWIN uname patterns
- Output filename automatically gets .exe suffix on Windows in build.sh

---
Task ID: 5
Agent: system-proxy-fixer
Task: Fix Windows system proxy: ProxyOverride, constants, AutoConfigURL

Work Log:
- Added ProxyOverride bypass list for Windows proxy
- Replaced magic numbers with named constants
- Clear AutoConfigURL when setting manual proxy

Stage Summary:
- Windows proxy now sets bypass list to prevent localhost loops
- Code uses named constants instead of magic numbers
- PAC auto-config is cleared when manual proxy is enabled

---
Task ID: 3
Agent: auto-updater-fixer
Task: Fix auto_updater.py Windows support (.zip + sing-box.exe)

Work Log:
- Added .zip archive support alongside .tar.gz in install_core_update()
- Added platform-aware binary_name: "sing-box.exe" on Windows, "sing-box" elsewhere
- All binary name references (search, extract, rename, dest path) now use binary_name
- Wrapped chmod in platform.system() check (meaningless on Windows)
- Fixed download_and_verify() temp file suffix: derived from URL (.tar.gz/.tgz/.zip) instead of hardcoded ".zip"
- Added unsupported archive format error for unknown extensions

Stage Summary:
- auto_updater now supports both .tar.gz (Linux/macOS) and .zip (Windows) archives
- Binary name correctly handled as sing-box.exe on Windows
- chmod only runs on non-Windows platforms
- Download temp file gets correct suffix matching the actual archive format

---
Task ID: 2
Agent: path-and-process-fixer
Task: Fix Windows path handling and process management

Work Log:
- Replaced Path.home() / ".venlta" with get_data_dir() in config_manager.py, database.py, singbox_manager.py, system_proxy.py
- Added backward compatibility: if ~/.venlta exists, prefer it
- Added Windows zombie process cleanup using taskkill
- Fixed SIGTERM/SIGKILL to use taskkill on Windows
- Added Windows-specific port conflict error message
- Added SQLite timeout parameter

Stage Summary:
- All path references now use get_data_dir() with backward compat
- Windows process management now uses taskkill instead of Unix signals
- SQLite has proper timeout for Windows file locking

---
Task ID: 7
Agent: Main
Task: Fix Vite build failure (qwebchannel.js) + tray icon visibility

Work Log:
- Identified root cause of Vite build failure: `<script src="qrc:///qtwebchannel/qwebchannel.js">` in index.html cannot be resolved by Vite
- Fixed index.html: replaced static qrc:// script tag with conditional `document.write()` that only loads in Qt environment (checks `window.qt`)
- Fixed vite.config.ts: added `base: './'` for relative asset paths (required for Qt file:// loading), explicit `outDir: 'dist'`
- Fixed caniuse-lite broken packages (missing lib/statuses.js and lib/supported.js) — created polyfill files
- Fixed tray icon: replaced small corner dot badge with full background color status icon
  - Stopped = gray background, Running = green, System Proxy = blue, TUN = red-brown, Both = purple
  - White logo silhouette on colored background (using CompositionMode_SourceIn)
  - Much more visible than previous 8px dot
- Verified: `vite build` succeeds, dist/index.html is generated correctly

Stage Summary:
- Vite build now works (was completely broken before due to qrc:// URL)
- dist/ output generated: index.html + assets/ (CSS + JS)
- Tray icon uses background color instead of small dot — clearly visible at any size
- qwebchannel.js still loads in Qt environment via conditional document.write()
