"""系统托盘实现（NekoBox 风格）

系统托盘提供最小化到托盘、独立切换系统代理/TUN、重启代理、状态图标切换等功能。

参考 NekoBox 的 tray 设计：
- menu_spmode 子菜单：System Proxy / TUN / Disabled（互斥单选）
- 动态重建菜单（aboutToShow 时刷新状态）
- Trigger（单击）切换窗口显示
- 多状态图标切换

托盘图标文件（SVG 源文件 → PNG，由 generate_icons.py 转换）：
- venlta-stopped.png   深蓝灰底 — 未启动代理
- venlta-running.png   深红底   — 启动了代理（系统代理）
- venlta-tun.png       深绿底   — 启动了 TUN
- venlta-both.png      深紫底   — 系统代理 + TUN 都启动
"""

import logging

from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction, QActionGroup
from PySide6.QtCore import QObject, Signal
from pathlib import Path
from utils.i18n import t
import sys

logger = logging.getLogger(__name__)

# 状态 → 图标文件名 映射
ICON_STOPPED = "venlta-stopped.png"       # 深蓝灰底 — 未启动
ICON_RUNNING = "venlta-running.png"       # 深红底 — 系统代理
ICON_TUN = "venlta-tun.png"               # 深绿底 — TUN
ICON_BOTH = "venlta-both.png"             # 深紫底 — 两者都启动
ICON_FALLBACK = "venlta.png"


class SystemTray(QObject):
    """系统托盘管理器（NekoBox 风格）"""

    # 信号：用户通过托盘触发的操作
    show_window_requested = Signal()
    quit_requested = Signal()
    toggle_system_proxy_requested = Signal(bool)   # True=开启, False=关闭
    toggle_tun_requested = Signal(bool)             # True=开启, False=关闭
    restart_proxy_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tray = QSystemTrayIcon()
        self._menu = QMenu()

        # 代理状态追踪（由 set_proxy_state 更新）
        self._is_running = False
        self._is_system_proxy_enabled = False
        self._is_tun_enabled = False

        # 预加载所有状态图标（避免运行时 IO）
        self._icons_dir = self._find_icons_dir()
        self._icon_cache: dict[str, QIcon] = {}
        self._preload_icons()

        # 构建菜单
        self._setup_menu()
        self._tray.setContextMenu(self._menu)

        # 动态重建菜单：每次显示时刷新 checkbox/action 状态
        self._menu.aboutToShow.connect(self._on_menu_about_to_show)

        # 托盘图标点击行为（NekoBox: Trigger = 单击切换窗口）
        self._tray.activated.connect(self._on_activated)

        # 设置初始图标和提示
        self._update_icon()
        self._update_tooltip()

    def _preload_icons(self):
        """预加载所有状态图标到缓存"""
        for name in [ICON_STOPPED, ICON_RUNNING, ICON_TUN,
                     ICON_BOTH, ICON_FALLBACK, "venlta.ico"]:
            path = self._icons_dir / name
            if path.exists():
                self._icon_cache[name] = QIcon(str(path))

    def _get_icon(self, name: str) -> QIcon:
        """从缓存获取图标，未命中则尝试加载"""
        icon = self._icon_cache.get(name)
        if icon and not icon.isNull():
            return icon
        # 尝试加载
        path = self._icons_dir / name
        if path.exists():
            icon = QIcon(str(path))
            self._icon_cache[name] = icon
            return icon
        return QIcon()

    def _find_icons_dir(self) -> Path:
        """查找图标目录"""
        icons_dir = Path(__file__).parent / "resources" / "icons"
        if icons_dir.exists():
            return icons_dir
        project_root = Path(__file__).parent.parent
        icons_dir = project_root / "resources" / "icons"
        if icons_dir.exists():
            return icons_dir
        return Path(sys.executable).parent / "resources" / "icons"

    def _setup_menu(self):
        """构建托盘右键菜单（NekoBox 风格）"""
        # 显示主窗口
        self._show_action = QAction(t("tray.show_window"), self)
        self._show_action.triggered.connect(self.show_window_requested.emit)
        self._menu.addAction(self._show_action)

        self._menu.addSeparator()

        # 代理模式子菜单（NekoBox menu_spmode）
        self._spmode_menu = QMenu(t("tray.proxy_mode"), self._menu)

        # 单选组：System Proxy / TUN / Disabled
        self._spmode_group = QActionGroup(self)
        self._spmode_group.setExclusive(False)  # 不互斥，可同时开启

        self._action_system_proxy = QAction(t("tray.system_proxy"), self)
        self._action_system_proxy.setCheckable(True)
        self._action_system_proxy.triggered.connect(self._on_toggle_system_proxy)
        self._spmode_group.addAction(self._action_system_proxy)
        self._spmode_menu.addAction(self._action_system_proxy)

        self._action_tun = QAction(t("tray.tun_mode"), self)
        self._action_tun.setCheckable(True)
        self._action_tun.triggered.connect(self._on_toggle_tun)
        self._spmode_group.addAction(self._action_tun)
        self._spmode_menu.addAction(self._action_tun)

        self._spmode_menu.addSeparator()

        self._action_disabled = QAction(t("tray.disable_all"), self)
        self._action_disabled.triggered.connect(self._on_disable_all)
        self._spmode_menu.addAction(self._action_disabled)

        self._menu.addMenu(self._spmode_menu)

        # 重启代理
        self._restart_action = QAction(t("tray.restart_proxy"), self)
        self._restart_action.triggered.connect(self.restart_proxy_requested.emit)
        self._menu.addAction(self._restart_action)

        self._menu.addSeparator()

        # 退出
        self._quit_action = QAction(t("tray.quit"), self)
        self._quit_action.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(self._quit_action)

    def _on_menu_about_to_show(self):
        """菜单即将显示时刷新 action 状态（NekoBox 动态重建模式）"""
        self._action_system_proxy.setChecked(self._is_system_proxy_enabled)
        self._action_tun.setChecked(self._is_tun_enabled)
        # 代理未运行时禁用"重启代理"，运行中才允许重启
        self._restart_action.setEnabled(self._is_running)
        # 重建菜单文本（语言可能已切换）
        self._refresh_menu_text()

    def _refresh_menu_text(self):
        """刷新所有菜单项文本（语言切换后调用）"""
        self._show_action.setText(t("tray.show_window"))
        self._spmode_menu.setTitle(t("tray.proxy_mode"))
        self._action_system_proxy.setText(t("tray.system_proxy"))
        self._action_tun.setText(t("tray.tun_mode"))
        self._action_disabled.setText(t("tray.disable_all"))
        self._restart_action.setText(t("tray.restart_proxy"))
        self._quit_action.setText(t("tray.quit"))
        self._update_tooltip()

    def _on_activated(self, reason):
        """托盘图标点击事件（NekoBox 风格：单击切换窗口）"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # 单击：切换窗口显示（NekoBox 行为）
            self.show_window_requested.emit()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            # 双击也切换窗口（兼容）
            self.show_window_requested.emit()

    def _on_toggle_system_proxy(self, checked: bool):
        """切换系统代理"""
        self.toggle_system_proxy_requested.emit(checked)

    def _on_toggle_tun(self, checked: bool):
        """切换 TUN 模式"""
        self.toggle_tun_requested.emit(checked)

    def _on_disable_all(self):
        """关闭所有代理模式"""
        if self._is_system_proxy_enabled:
            self.toggle_system_proxy_requested.emit(False)
        if self._is_tun_enabled:
            self.toggle_tun_requested.emit(False)

    def _get_status_icon_name(self) -> str:
        """根据当前状态返回对应的图标文件名

        图标映射：
        - 未启动代理        → venlta-stopped  (深蓝灰底)
        - 启动了系统代理     → venlta-running  (深红底)
        - 启动了 TUN        → venlta-tun      (深绿底)
        - 系统代理 + TUN    → venlta-both     (深紫底)
        - 代理运行但都没开   → venlta-running  (深红底，默认)
        """
        if not self._is_running:
            return ICON_STOPPED
        if self._is_system_proxy_enabled and self._is_tun_enabled:
            return ICON_BOTH
        elif self._is_tun_enabled:
            return ICON_TUN
        elif self._is_system_proxy_enabled:
            return ICON_RUNNING
        else:
            # 代理运行中但未开启系统代理/TUN，默认使用 running 图标
            return ICON_RUNNING

    def _update_icon(self):
        """根据代理状态切换托盘图标（直接加载预生成的图标文件）"""
        icon_name = self._get_status_icon_name()
        icon = self._get_icon(icon_name)

        if not icon.isNull():
            self._tray.setIcon(icon)
            return

        # 回退：原始 venlta.png / .ico
        for fb in [ICON_FALLBACK, "venlta.ico"]:
            icon = self._get_icon(fb)
            if not icon.isNull():
                self._tray.setIcon(icon)
                return

        logger.warning("No tray icon found in %s", self._icons_dir)

    def _update_tooltip(self):
        """根据代理状态更新托盘提示文本（NekoBox 风格：显示模式信息）"""
        if not self._is_running:
            self._tray.setToolTip(t("tray.tooltip_stopped"))
            return

        # 构建模式标签
        mode_parts = []
        if self._is_system_proxy_enabled:
            mode_parts.append(t("tray.mode_system_proxy"))
        if self._is_tun_enabled:
            mode_parts.append(t("tray.mode_tun"))

        if mode_parts:
            mode_str = " + ".join(mode_parts)
            self._tray.setToolTip(f"Venlta - {mode_str}")
        else:
            self._tray.setToolTip(t("tray.tooltip_running"))

    def set_proxy_state(self, running: bool, system_proxy_enabled: bool = None, tun_enabled: bool = None):
        """更新代理运行状态（由后端调用）

        Args:
            running: sing-box 是否运行中
            system_proxy_enabled: 系统代理是否开启（None=不更新）
            tun_enabled: TUN 是否开启（None=不更新）
        """
        self._is_running = running
        if system_proxy_enabled is not None:
            self._is_system_proxy_enabled = system_proxy_enabled
        if tun_enabled is not None:
            self._is_tun_enabled = tun_enabled

        self._update_icon()
        self._update_tooltip()
        # 同步"重启代理"菜单项的启用状态
        self._restart_action.setEnabled(self._is_running)

    def rebuild_menu(self):
        """语言切换后重建菜单文本（由 VenltaBridge.setBackendLanguage 调用）"""
        self._refresh_menu_text()

    def show(self):
        """显示托盘图标"""
        self._tray.show()

    def hide(self):
        """隐藏托盘图标"""
        self._tray.hide()

    def show_notification(self, title: str, message: str, duration_ms: int = 3000):
        """显示托盘通知"""
        self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, duration_ms)
