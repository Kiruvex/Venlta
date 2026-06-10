"""后端轻量 i18n 模块

自动检测系统语言，提供 t(key, **kwargs) 翻译函数。
与前端 i18n 独立，因为打包后前端 JSON 不一定可访问。
"""

import locale
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── 语言检测 ──────────────────────────────────────────────

def _detect_language() -> str:
    """检测系统语言，返回 'zh' / 'en' 等短代码"""
    try:
        from PySide6.QtCore import QLocale
        lang = QLocale.system().name()  # e.g. "zh_CN", "en_US"
        return lang.split("_")[0].lower()
    except Exception:
        pass
    try:
        # locale.getdefaultlocale() 在 Python 3.11 已弃用，3.15 已移除
        # 使用 locale.getlocale() 替代
        loc = locale.getlocale()
        lang = loc[0] if loc and loc[0] else None  # e.g. "zh_CN"
        if lang:
            return lang.split("_")[0].lower()
    except Exception:
        pass
    return "en"

_current_lang: str = _detect_language()

def set_language(lang: str) -> None:
    """切换后端语言"""
    global _current_lang
    _current_lang = lang.split("_")[0].lower() if lang else "en"

def get_language() -> str:
    """获取当前后端语言"""
    return _current_lang

# ── 翻译字典 ──────────────────────────────────────────────

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh": {
        # 托盘菜单
        "tray.show_window": "显示主窗口",
        "tray.start_proxy": "启动代理",
        "tray.stop_proxy": "停止代理",
        "tray.quit": "退出",
        # 托盘提示
        "tray.tooltip_running": "Venlta - 代理运行中",
        "tray.tooltip_stopped": "Venlta - 代理已停止",
        # 托盘通知
        "tray.notification_minimized_title": "Venlta",
        "tray.notification_minimized": "应用已最小化到系统托盘",
        # 更新通知
        "tray.notification_update_title": "发现新版本",
        "tray.notification_update_available": "Venlta {{version}} 已发布，请前往设置页面更新",
        "tray.notification_core_update_title": "核心更新可用",
        "tray.notification_core_update_available": "sing-box {{version}} 已发布",
        # 启动错误
        "error.start_failed": "启动代理失败",
        "error.config_invalid": "配置无效，请检查设置",
        "error.port_in_use": "端口 {{port}} 被占用",
    },
    "en": {
        # Tray menu
        "tray.show_window": "Show Window",
        "tray.start_proxy": "Start Proxy",
        "tray.stop_proxy": "Stop Proxy",
        "tray.quit": "Quit",
        # Tray tooltip
        "tray.tooltip_running": "Venlta - Proxy Running",
        "tray.tooltip_stopped": "Venlta - Proxy Stopped",
        # Tray notification
        "tray.notification_minimized_title": "Venlta",
        "tray.notification_minimized": "Application minimized to system tray",
        # Update notification
        "tray.notification_update_title": "Update Available",
        "tray.notification_update_available": "Venlta {{version}} is available. Go to Settings to update.",
        "tray.notification_core_update_title": "Core Update Available",
        "tray.notification_core_update_available": "sing-box {{version}} is available",
        # Startup errors
        "error.start_failed": "Failed to start proxy",
        "error.config_invalid": "Invalid configuration, please check settings",
        "error.port_in_use": "Port {{port}} is in use",
    },
}

# ── 翻译函数 ──────────────────────────────────────────────

def t(key: str, **kwargs: Any) -> str:
    """翻译 key 为当前语言文本，支持 {{placeholder}} 插值

    Args:
        key: 翻译键，如 "tray.show_window"
        **kwargs: 插值参数，如 count=3 → {{count}} 替换为 "3"

    Returns:
        翻译后的字符串；找不到时返回 key 本身
    """
    lang_dict = _TRANSLATIONS.get(_current_lang, {})
    text = lang_dict.get(key)
    if text is None:
        # 回退到英文
        text = _TRANSLATIONS.get("en", {}).get(key, key)
    # 简易插值：{{name}} → kwargs[name]
    if kwargs:
        for k, v in kwargs.items():
            text = text.replace("{{" + k + "}}", str(v))
    return text
