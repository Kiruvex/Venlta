import sys
import os

# 确保 backend/ 目录在 sys.path 中，使得 from bridge.xxx / from core.xxx 等导入
# 在 python -m backend.main 和直接 python backend/main.py 两种启动方式下都能工作
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

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

    def _on_tray_toggle_proxy(self, start: bool):
        """托盘菜单触发的代理启停

        与 VenltaBridge.startProxy/stopProxy 保持一致：
        - 停止时同时关闭系统代理，避免操作系统代理仍指向已关闭的端口
        - 启动时若 TUN 未启用则自动设置系统代理，确保流量走代理
        """
        if start:
            self.singbox_mgr.start()
            # 若 TUN 未启用，设置系统代理确保流量路由
            # 使用 mixed inbound（同时提供 HTTP+SOCKS5），端口均为 http_port
            tun_enabled = self.config_mgr.get_tun_enabled()
            if not tun_enabled:
                http_port = self.db.get_setting('http_port', 10809)
                self.sys_proxy.set_enabled(True, port=http_port, socks_port=http_port)
        else:
            self.singbox_mgr.stop()
            # 停止代理时必须恢复系统代理设置，否则操作系统代理仍指向已关闭的端口
            self.sys_proxy.set_enabled(False)

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
    # 使用 sys.executable 路径推导：Nuitka 打包后 __file__ 可能指向临时解压目录，
    # 而 sys.executable 始终指向实际可执行文件位置
    if os.getenv("VENLTA_DEV"):
        url = QUrl("http://localhost:5173")
    else:
        static_path = os.path.join(os.path.dirname(sys.executable), "frontend")
        url = QUrl.fromLocalFile(os.path.join(static_path, "index.html"))
    webview.load(url)

    # 创建主窗口（集成 WebView）
    window = MainWindow(bridge, singbox_mgr, config_mgr, sys_proxy, db, tray=None)
    window.setCentralWidget(webview)
    window.setWindowTitle("Venlta")
    window.resize(1000, 700)

    # 初始化系统托盘
    # Read saved language preference from database, apply to backend before creating tray
    # so tray menus are in the correct language from the start
    saved_language = db.get_setting('language')
    if saved_language:
        from utils.i18n import set_language
        set_language(saved_language)
    tray = SystemTray(window)
    window.tray = tray
    tray.show_window_requested.connect(window.show_and_activate)
    tray.quit_requested.connect(window._on_quit)
    tray.toggle_proxy_requested.connect(window._on_tray_toggle_proxy)
    tray.show()

    # 代理状态变更时同步更新托盘图标
    def on_proxy_state_changed(state_json: str):
        try:
            import json
            result = json.loads(state_json)
            if result.get("ok") and result.get("data"):
                tray.set_proxy_state(result["data"].get("isRunning", False))
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
    # sing-box always starts when the app launches if there are enabled nodes,
    # so the user doesn't have to manually click "Start" every time.
    def _startup_auto_start():
        try:
            # Check if there are enabled nodes before starting
            nodes = db.get_all_nodes_raw()
            enabled_nodes = [n for n in nodes if n.get('is_enabled', 0)]
            if enabled_nodes:
                logger.info(f"Auto-starting proxy ({len(enabled_nodes)} enabled nodes)...")
                singbox_mgr.start()
                # 若 TUN 未启用，设置系统代理确保流量路由
                # 使用 mixed inbound（同时提供 HTTP+SOCKS5），端口均为 http_port
                tun_enabled = config_mgr.get_tun_enabled()
                if not tun_enabled:
                    http_port = db.get_setting('http_port', 10809)
                    sys_proxy.set_enabled(True, port=http_port, socks_port=http_port)
                logger.info("Auto-start completed successfully")
            else:
                logger.info("No enabled nodes, skipping auto-start")
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

    logger.info("Venlta window created. All modules initialized. Waiting for frontend...")
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
