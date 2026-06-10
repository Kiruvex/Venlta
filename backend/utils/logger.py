"""日志配置：RotatingFileHandler + StreamHandler"""

import logging
import os
from logging.handlers import RotatingFileHandler
from utils.constants import get_data_dir

def setup_logger(level=logging.DEBUG):
    """初始化应用日志系统

    配置根日志器，同时输出到文件和控制台：
    - 文件：轮转日志，单文件 5MB，保留 3 个备份
    - 控制台：DEBUG 及以上级别
    """
    log_dir = os.path.join(get_data_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "venlta.log")

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 避免重复添加 handler（HMR 或多次调用时）
    if root_logger.handlers:
        return

    # 文件 handler（轮转，5MB/文件，保留 3 个备份）
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)

    # 控制台 handler
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    stream_formatter = logging.Formatter(
        "[%(levelname)s] %(name)s: %(message)s"
    )
    stream_handler.setFormatter(stream_formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)

    # 抑制第三方库的 DEBUG 日志刷屏（httpcore/httpx 的连接细节无需输出到控制台）
    for noisy in ("httpcore", "httpx", "hpack", "h2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
