"""常量定义：默认端口、路径等"""

import os
import sys

# 默认端口
DEFAULT_SOCKS_PORT = 10808
DEFAULT_HTTP_PORT = 10809
DEFAULT_MAPPED_PORT = 10810
DEFAULT_REDIR_PORT = 10811
DEFAULT_TPROXY_PORT = 10812
DEFAULT_MIXED_PORT = 10813
DEFAULT_CLASH_API_PORT = 9090
DEFAULT_DNS_PORT = 5353

# Clash API
CLASH_API_HOST = "127.0.0.1"
CLASH_API_BASE_URL = f"http://{CLASH_API_HOST}:{DEFAULT_CLASH_API_PORT}"
CLASH_API_SECRET_DEFAULT = "venlta"

# 数据库
DB_FILENAME = "venlta.db"

# 配置
SINGBOX_CONFIG_FILENAME = "config.json"
SINGBOX_CONFIG_BACKUP_FILENAME = "config.json.bak"

# 代理选择器 tag
PROXY_SELECTOR_TAG = "proxy"

# 路径
def get_data_dir() -> str:
    """获取应用数据目录"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "Venlta")
    else:
        base = os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share"))
        return os.path.join(base, "Venlta")

def get_config_dir() -> str:
    """获取配置目录"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "Venlta")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
        return os.path.join(base, "Venlta")

# 应用信息
APP_NAME = "Venlta"
APP_VERSION_FILE = "VERSION"
