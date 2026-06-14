import sys
import os
import json

# 确保 backend/ 目录在 sys.path 中，使得 from bridge.xxx / from core.xxx 等导入
# 在 python -m backend.main 和直接 python backend/main.py 两种启动方式下都能工作
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# faulthandler: 在 C 级别崩溃时打印 Python 调用栈，便于诊断 glibc 堆损坏等问题
# 必须在 PySide6 导入之前启用，否则崩溃时可能来不及输出
try:
    import faulthandler
    faulthandler.enable()
except ImportError:
    pass

# QtWebEngine Chromium 标志：禁用代理自动检测，防止 TUN 模式启用时
# Chromium 代理解析代码检测到网络变化导致浏览器进程崩溃
# 必须在 QWebEngineView 导入之前设置
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-features=NetworkServiceInProcess "
    "--no-proxy-server")

from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import QUrl, Qt
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from bridge.venlta_bridge import VenltaBridge
from core.database import DatabaseManager
from core.config_manager import ConfigManager
from core.singbox_manager import SingboxManager
from core.system_proxy import SystemProxy
from core.tun_elevator import TunElevator
from core.subscription import SubscriptionManager
from core.stats_collector import StatsCollector
from core.auto_updater import AutoUpdater
from core.port_detector import PortDetector
from core.speed_tester import SpeedTester
from tray import SystemTray
from utils.logger import setup_logger
from utils.i18n import t
import logging


class MainWindow(QMainWindow):
    """Venlta 主窗口

    集成系统托盘：关闭窗口时最小化到托盘而非退出，
    用户可通过托盘右键菜单退出应用。
    """

    def __init__(self, bridge, singbox_mgr, config_mgr, sys_proxy, db, tray):
        super().__init__()
        self.bridge = bridge
        self.singbox_mgr = singbox_mgr
        self.config_mgr = config_mgr
        self.sys_proxy = sys_proxy
        self.db = db
        self.tray = tray
        self._quitting = False  # 标记是否真正退出（而非最小化到托盘）

    def show_and_activate(self):
        """显示并激活主窗口（从托盘恢复）"""
        self.show()
        self.activateWindow()
        self.raise_()

    def _on_tray_toggle_system_proxy(self, enabled: bool):
        """托盘菜单触发的系统代理切换

        通过 bridge.toggleSystemProxy() 执行，确保互斥锁保护，
        避免与前端并发操作产生竞态条件。
        """
        self.bridge.toggleSystemProxy(enabled)

    def _on_tray_toggle_tun(self, enabled: bool):
        """托盘菜单触发的 TUN 切换

        通过 bridge.toggleTun() 执行，确保互斥锁保护。
        """
        self.bridge.toggleTun(enabled)

    def _on_tray_restart_proxy(self):
        """托盘菜单触发的重启代理"""
        self.bridge.restartProxy()

    def _on_quit(self):
        """托盘菜单触发的退出

        直接调用 QApplication.quit() 而非 self.close()。
        原因：窗口已隐藏时 self.close() 可能不触发 closeEvent，
        导致 _quitting 标记虽然设置了但窗口不关闭、应用不退出。
        QApplication.quit() 会触发 aboutToQuit 信号执行清理，然后退出事件循环。
        """
        self._quitting = True
        QApplication.quit()

    def closeEvent(self, event):
        """关闭事件：最小化到托盘而非退出

        点击窗口关闭按钮 → 隐藏窗口 + 显示托盘通知
        通过托盘"退出"按钮 → _on_quit() 直接调用 QApplication.quit()
        此处仅处理窗口关闭按钮的场景。
        """
        if self._quitting:
            # 托盘退出触发的关闭，接受事件
            event.accept()
        else:
            # 最小化到托盘
            event.ignore()
            self.hide()
            self.tray.show_notification(t("tray.notification_minimized_title"), t("tray.notification_minimized"))


def main():
    # 高 DPI 支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Venlta")
    app.setOrganizationName("Venlta")

    setup_logger()
    logger = logging.getLogger(__name__)
    logger.info("Venlta starting...")

    # 初始化数据库（含 Migration）
    db = DatabaseManager()
    db.migrate()

    # 初始化各个管理器
    config_mgr = ConfigManager(db)

    # 获取 Clash API secret（需要在创建 SingboxManager 之前）
    clash_api_secret = config_mgr.get_clash_api_secret()

    # sing-box 管理器（内部已创建 QThread 和 Worker，无需外部 moveToThread）
    tun_elevator = TunElevator()
    singbox_mgr = SingboxManager(config_mgr, clash_api_secret, tun_elevator)

    sys_proxy = SystemProxy()
    sub_mgr = SubscriptionManager(db, config_mgr)

    # 统计采集器（内部已创建 QThread 和 Worker）
    # 传入 Clash API secret 以通过认证（复用上面已获取的 clash_api_secret）
    stats = StatsCollector(singbox_mgr, clash_api_secret)
    stats.start()  # 启动统计采集线程

    updater = AutoUpdater()
    # 将版本号写入数据库，供 SettingsPage 读取显示
    db.update_setting('app_version', updater.current_version)
    db.update_setting('singbox_version', updater._get_current_singbox_version())

    # 端口检测器
    port_detector = PortDetector()

    # 速度测试器（门面模式，封装 SingboxManager.test_latency 添加并发控制和结果聚合）
    speed_tester = SpeedTester(singbox_mgr, db)

    # 创建 WebView 和 WebChannel
    webview = QWebEngineView()
    channel = QWebChannel()
    bridge = VenltaBridge(
        singbox_mgr=singbox_mgr, config_mgr=config_mgr, sys_proxy=sys_proxy,
        tun_elevator=tun_elevator, sub_mgr=sub_mgr, stats=stats,
        db=db, updater=updater, port_detector=port_detector,
        speed_tester=speed_tester
    )
    channel.registerObject("bridge", bridge)
    webview.page().setWebChannel(channel)

    # 加载前端
    # 开发模式加载 Vite dev server，生产模式加载打包后的静态文件
    # 路径解析策略：
    # - Nuitka --standalone: 数据文件在 <exe_dir>/frontend/（sys.executable 同级目录）
    # - Nuitka --onefile: 数据文件解压到临时目录（__file__ 指向临时目录）
    # - 开发环境: frontend/dist/ 在项目根目录
    if os.getenv("VENLTA_DEV"):
        url = QUrl("http://localhost:5173")
    else:
        index_html = None
        # 1. Nuitka --standalone: frontend/ 在可执行文件同级目录
        exe_frontend = os.path.join(os.path.dirname(sys.executable), "frontend", "index.html")
        if os.path.isfile(exe_frontend):
            index_html = exe_frontend
        # 2. Nuitka --onefile: frontend/ 在临时解压目录（__file__ 推导）
        if not index_html:
            onefile_frontend = os.path.join(_BACKEND_DIR, "frontend", "index.html")
            if os.path.isfile(onefile_frontend):
                index_html = onefile_frontend
        # 3. 开发环境: frontend/dist/index.html 在项目根目录
        if not index_html:
            project_root = os.path.dirname(_BACKEND_DIR)
            dev_frontend = os.path.join(project_root, "frontend", "dist", "index.html")
            if os.path.isfile(dev_frontend):
                index_html = dev_frontend
        # 4. 兜底: frontend/index.html
        if not index_html:
            project_root = os.path.dirname(_BACKEND_DIR)
            fallback_frontend = os.path.join(project_root, "frontend", "index.html")
            if os.path.isfile(fallback_frontend):
                index_html = fallback_frontend
        if index_html:
            url = QUrl.fromLocalFile(index_html)
        else:
            logger.error("Frontend index.html not found in any search path")
            url = QUrl("about:blank")
    webview.load(url)

    # 创建主窗口（集成 WebView）
    window = MainWindow(bridge, singbox_mgr, config_mgr, sys_proxy, db, tray=None)
    window.setCentralWidget(webview)
    window.setWindowTitle("Venlta")
    window.resize(1000, 700)

    # 设置窗口图标
    try:
        from PySide6.QtGui import QIcon
        icon = QIcon()
        # 搜索图标路径（兼容 standalone / onefile / 开发环境）
        icon_search_paths = [
            Path(sys.executable).parent / "resources" / "icons" / "venlta.png",
            Path(__file__).parent.parent / "resources" / "icons" / "venlta.png",
            Path(__file__).parent / "resources" / "icons" / "venlta.png",
        ]
        for icon_path in icon_search_paths:
            if icon_path.exists():
                icon = QIcon(str(icon_path))
                break
        if not icon.isNull():
            window.setWindowIcon(icon)
            app.setWindowIcon(icon)
    except Exception as e:
        logger.debug(f"Failed to set window icon: {e}")

    # 初始化系统托盘
    # Read saved language preference from database, apply to backend before creating tray
    # so tray menus are in the correct language from the start
    saved_language = db.get_setting('language')
    if saved_language:
        from utils.i18n import set_language
        set_language(saved_language)
    tray = SystemTray(window)
    window.tray = tray
    tray.show()

    # 连接托盘信号到 MainWindow 的槽函数
    tray.show_window_requested.connect(window.show_and_activate)
    tray.quit_requested.connect(window._on_quit)
    tray.toggle_system_proxy_requested.connect(window._on_tray_toggle_system_proxy)
    tray.toggle_tun_requested.connect(window._on_tray_toggle_tun)
    tray.restart_proxy_requested.connect(window._on_tray_restart_proxy)

    # 代理状态变更时同步更新托盘图标（含模式信息）
    def on_proxy_state_changed(state_json: str):
        try:
            import json
            result = json.loads(state_json)
            if result.get("ok") and result.get("data"):
                data = result["data"]
                tray.set_proxy_state(
                    running=data.get("isRunning", False),
                    system_proxy_enabled=data.get("isSystemProxyEnabled", False),
                    tun_enabled=data.get("isTunEnabled", False),
                )
        except Exception:
            pass
    bridge.proxyStateChanged.connect(on_proxy_state_changed)

    # 将托盘引用注入 bridge，供 setBackendLanguage() 调用 tray.rebuild_menu()
    bridge._tray = tray

    # Auto-update check on startup (delayed to avoid slowing down launch)
    from PySide6.QtCore import QTimer
    def _startup_update_check():
        try:
            auto_enabled = db.get_setting('auto_update_enabled', False)
            if auto_enabled:
                logger.info("Auto-update enabled, checking for updates...")
                bridge.checkAndNotifyUpdates()
        except Exception as e:
            logger.debug(f"Startup update check failed: {e}")
    QTimer.singleShot(5000, _startup_update_check)

    # Auto-start sing-box on app launch (delayed to allow UI to initialize first)
    # 默认不自动启动代理，用户需要手动开启。
    # 如果用户开启了 auto_start_proxy 设置，才会在启动时自动开启上次使用的模式。
    def _startup_auto_start():
        try:
            auto_start = config_mgr.db.get_setting('auto_start_proxy', False)
            if not auto_start:
                # 默认不自动启动：重置系统代理和 TUN 的启用状态
                # 这样前端显示的状态与实际一致（都已关闭）
                config_mgr.db.update_setting('system_proxy_enabled', False)
                config_mgr.db.update_setting('tun_enabled', False)
                logger.info("Auto-start proxy disabled, resetting system_proxy and TUN to off")
                return

            sys_proxy_enabled = config_mgr.db.get_setting('system_proxy_enabled', False)
            tun_enabled = config_mgr.get_tun_enabled()
            if not (sys_proxy_enabled or tun_enabled):
                logger.info("Both system proxy and TUN are off, skipping auto-start")
                return
            # Check if there are enabled nodes before starting
            nodes = db.get_all_nodes_raw()
            enabled_nodes = [n for n in nodes if n.get('is_enabled', 0)]
            if not enabled_nodes:
                logger.info("No enabled nodes, skipping auto-start")
                return
            logger.info(f"Auto-starting proxy ({len(enabled_nodes)} enabled nodes, sys_proxy={sys_proxy_enabled}, tun={tun_enabled})...")
            singbox_mgr.start()
            # 根据 system_proxy_enabled 设置决定是否开启系统代理
            # TUN 模式通过 sing-box 配置自动生效
            if sys_proxy_enabled:
                http_port = db.get_setting('http_port', 10809)
                sys_proxy.set_enabled(True, port=http_port, socks_port=http_port)
            logger.info("Auto-start completed successfully")
        except Exception as e:
            logger.error(f"Startup auto-start failed: {e}", exc_info=True)
            # 同步代理状态到前端，避免前端显示与实际不一致
            try:
                bridge.proxyStateChanged.emit(json.dumps({"ok": True, "data": {"isRunning": False}}))
            except Exception:
                pass
    QTimer.singleShot(3000, _startup_auto_start)

    window.show()

    # 应用退出清理：停止所有 QThread Worker，恢复系统代理
    def cleanup():
        from PySide6.QtCore import QMetaObject
        # 存根 Worker 没有 stop_singbox 方法，需安全检查
        if hasattr(singbox_mgr.worker, 'stop_singbox'):
            QMetaObject.invokeMethod(singbox_mgr.worker, "stop_singbox", Qt.BlockingQueuedConnection)
        if hasattr(singbox_mgr, 'workerThread'):
            singbox_mgr.workerThread.quit()
            singbox_mgr.workerThread.wait(3000)
        stats.stop()
        # 注意：不再需要手动清理 TUN 设备。
        # sing-box 进程退出时会自动销毁 TUN 接口和清理路由，
        # 无论是正常退出还是被 kill，操作系统都会回收 TUN 设备资源。
        db.close_all()
        sys_proxy.set_enabled(False)
    app.aboutToQuit.connect(cleanup)

    # 使用 os._exit() 代替正常退出，绕过 PySide6 的 destroyQCoreApplication
    # 清理逻辑。该清理在 Python 3.14 + PySide6 环境下会触发 glibc 堆损坏
    # （corrupted size vs. prev_size），这是 PySide6 上游 bug，无法从 Venlta 修复。
    # os._exit() 直接终止进程，跳过 Python 解释器清理和 atexit 处理器，
    # 避免触发有 bug 的 PySide6 析构代码。
    import os as _os

    def _clean_exit():
        """执行清理后立即终止进程，避免 PySide6 退出时 glibc 堆损坏"""
        try:
            cleanup()
        except Exception:
            pass
        _os._exit(0)

    app.aboutToQuit.disconnect(cleanup)
    app.aboutToQuit.connect(_clean_exit)

    logger.info("Venlta window created. All modules initialized. Waiting for frontend...")
    ret = app.exec()
    _os._exit(ret)

if __name__ == "__main__":
    main()
