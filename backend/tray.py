"""系统托盘实现

系统托盘提供最小化到托盘、快速启停代理、状态图标切换等功能。

托盘图标：
- backend/resources/icons/venlta.ico（Windows，含 16x16/32x32/48x48 多尺寸）
- backend/resources/icons/venlta.png（Linux，32x32）
- backend/resources/icons/venlta-connected.png（代理运行中）
- backend/resources/icons/venlta-disconnected.png（代理已停止）
"""

import logging

from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QObject, Signal
from pathlib import Path
from utils.i18n import t
import sys

logger = logging.getLogger(__name__)


class SystemTray(QObject):
    """系统托盘管理器"""

    # 信号：用户通过托盘触发的操作
    show_window_requested = Signal()
    quit_requested = Signal()
    toggle_proxy_requested = Signal(bool)  # True=启动, False=停止

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tray = QSystemTrayIcon()
        self._menu = QMenu()
        self._proxy_running = False
        self._setup_menu()
        self._tray.setContextMenu(self._menu)

        # 托盘图标点击行为
        self._tray.activated.connect(self._on_activated)

        # 设置初始图标和提示
        self._update_icon()
        self._update_tooltip()

    def _setup_menu(self):
        """构建托盘右键菜单"""
        # 显示主窗口
        self._show_action = QAction(t("tray.show_window"), self)
        self._show_action.triggered.connect(self.show_window_requested.emit)
        self._menu.addAction(self._show_action)

        self._menu.addSeparator()

        # 代理启停
        self._toggle_action = QAction(t("tray.start_proxy"), self)
        self._toggle_action.triggered.connect(self._on_toggle_proxy)
        self._menu.addAction(self._toggle_action)

        self._menu.addSeparator()

        # 退出
        self._quit_action = QAction(t("tray.quit"), self)
        self._quit_action.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(self._quit_action)

    def _on_activated(self, reason):
        """托盘图标点击事件

        Windows: 双击显示主窗口
        Linux: 根据桌面环境行为不同，统一使用双击
        """
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window_requested.emit()

    def _on_toggle_proxy(self):
        """切换代理状态"""
        self._proxy_running = not self._proxy_running
        self.toggle_proxy_requested.emit(self._proxy_running)

    def _update_icon(self):
        """根据代理状态更新托盘图标"""
        # 图标目录：按优先级依次查找
        # 1. backend/resources/icons/（开发模式，从源码目录运行）
        # 2. <exe_dir>/resources/icons/（Nuitka 打包模式）
        # 3. 项目根目录 resources/icons/（开发模式备选路径）
        icons_dir = Path(__file__).parent / "resources" / "icons"
        if not icons_dir.exists():
            # 开发模式下从项目根目录查找（python -m backend.main 场景）
            project_root = Path(__file__).parent.parent
            icons_dir = project_root / "resources" / "icons"
        if not icons_dir.exists():
            icons_dir = Path(sys.executable).parent / "resources" / "icons"
        if self._proxy_running:
            icon_name = "venlta-connected.png"
        else:
            icon_name = "venlta-disconnected.png"

        # Windows 优先使用 .ico 格式（含多尺寸）
        if sys.platform == "win32":
            ico_path = icons_dir / "venlta.ico"
            if ico_path.exists():
                self._tray.setIcon(QIcon(str(ico_path)))
                return

        icon_path = icons_dir / icon_name
        if icon_path.exists():
            self._tray.setIcon(QIcon(str(icon_path)))
        else:
            # 图标文件不存在时，依次尝试 fallback 路径
            fallback_names = ["venlta.png", "venlta.ico"]
            fallback_found = False
            for fb in fallback_names:
                fb_path = icons_dir / fb
                if fb_path.exists():
                    self._tray.setIcon(QIcon(str(fb_path)))
                    fallback_found = True
                    logger.debug(f"Using fallback icon: {fb_path}")
                    break
            if not fallback_found:
                logger.warning(f"Tray icon not found at {icon_path}, using empty icon")

    def _update_tooltip(self):
        """根据代理状态更新托盘提示文本"""
        if self._proxy_running:
            self._tray.setToolTip(t("tray.tooltip_running"))
        else:
            self._tray.setToolTip(t("tray.tooltip_stopped"))

    def set_proxy_state(self, running: bool):
        """更新代理运行状态（由后端调用）"""
        self._proxy_running = running
        self._toggle_action.setText(t("tray.stop_proxy") if running else t("tray.start_proxy"))
        self._update_icon()
        self._update_tooltip()

    def rebuild_menu(self):
        """语言切换后重建菜单文本（由 VenltaBridge.setBackendLanguage 调用）"""
        self._show_action.setText(t("tray.show_window"))
        self._toggle_action.setText(t("tray.stop_proxy") if self._proxy_running else t("tray.start_proxy"))
        self._quit_action.setText(t("tray.quit"))
        self._update_tooltip()

    def show(self):
        """显示托盘图标"""
        self._tray.show()

    def hide(self):
        """隐藏托盘图标"""
        self._tray.hide()

    def show_notification(self, title: str, message: str, duration_ms: int = 3000):
        """显示托盘通知

        Args:
            title: 通知标题
            message: 通知内容
            duration_ms: 显示时长（毫秒）
        """
        self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, duration_ms)
