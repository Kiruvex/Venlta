"""
通用辅助函数
提供项目中多处复用的工具方法
"""
import re
import uuid
from typing import Optional


def generate_tag(prefix: str = "") -> str:
    """生成唯一标签，用于 sing-box 配置中的 tag 字段

    Args:
        prefix: 标签前缀，如 "node"、"group" 等

    Returns:
        格式为 prefix-xxxx 的唯一标签
    """
    short_id = uuid.uuid4().hex[:8]
    return f"{prefix}-{short_id}" if prefix else short_id


def parse_port_string(port_str: str) -> list[int]:
    """解析端口字符串，支持单端口、范围和逗号分隔

    示例:
        "80" -> [80]
        "80,443" -> [80, 443]
        "8080-8085" -> [8080, 8081, 8082, 8083, 8084, 8085]
        "80,443,8080-8082" -> [80, 443, 8080, 8081, 8082]
    """
    ports: list[int] = []
    for part in port_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            ports.extend(range(int(start), int(end) + 1))
        else:
            ports.append(int(part))
    return ports


def truncate_text(text: str, max_length: int = 50, suffix: str = "...") -> str:
    """截断过长文本，添加省略后缀

    Args:
        text: 原始文本
        max_length: 最大长度（含后缀）
        suffix: 截断后缀

    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符

    Args:
        name: 原始文件名

    Returns:
        合法文件名
    """
    # 移除 Windows/Linux 均不允许的字符
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()


def safe_int(value: Optional[str], default: int = 0) -> int:
    """安全地将字符串转换为整数，失败时返回默认值"""
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default
