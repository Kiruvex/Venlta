from cryptography.fernet import Fernet
import base64
import hashlib
import platform
import subprocess
import os
import logging
import threading

logger = logging.getLogger(__name__)

_key_derivation_failed = False  # 注意：此全局变量在多线程环境下不安全（非原子操作），但实际场景中 get_machine_key 仅在初始化时调用一次，后续只读取，风险可忽略
_cached_fernet_key: bytes | None = None  # 缓存 PBKDF2 派生的 Fernet 密钥，避免每次调用 get_cipher() 重新计算
_key_derivation_lock = threading.Lock()  # 保护 _key_derivation_failed 和 _cached_fernet_key 的并发访问

# PBKDF2 固定盐值（应用级常量，不随机器变化；安全性依赖机器指纹作为密码的不可预测性）
_PBKDF2_SALT = b"venlta-v1-key-derivation-salt"
_PBKDF2_ITERATIONS = 600000  # OWASP 2023 推荐的 PBKDF2-SHA256 最小迭代次数

def _derive_fernet_key(fingerprint: str) -> bytes:
    """使用 PBKDF2-HMAC-SHA256 从机器指纹派生 Fernet 密钥

    相比直接使用 SHA-256(fingerprint)，PBKDF2 增加 600,000 次迭代，
    使暴力破解/彩虹表攻击的计算成本提高约 60 万倍。
    返回值已为 base64url 编码的 44 字节字符串，可直接传给 Fernet()。
    """
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,  # Fernet 要求 32 字节密钥
        salt=_PBKDF2_SALT,
        iterations=_PBKDF2_ITERATIONS,
    )
    key = kdf.derive(fingerprint.encode())
    return base64.urlsafe_b64encode(key)  # 44 字节 base64url 字符串

def get_machine_key() -> bytes:
    """获取机器指纹并派生 Fernet 密钥

    注意：返回值是 base64url 编码的 Fernet 密钥（而非原始 SHA-256 摘要），
    由 _derive_fernet_key() 使用 PBKDF2 派生，可直接传给 Fernet() 构造器。
    保留此函数名是为了向后兼容内部调用约定。
    """
    global _key_derivation_failed, _cached_fernet_key
    # 快速路径：已缓存则直接返回（无需加锁，CPython GIL 保证引用赋值原子性）
    if _cached_fernet_key is not None:
        return _cached_fernet_key
    with _key_derivation_lock:
        # 双重检查：获取锁后再次确认（防止多线程同时通过首次检查）
        if _cached_fernet_key is not None:
            return _cached_fernet_key
        system = platform.system()
        fingerprint = ""

        if system == "Windows":
            try:
                output = subprocess.check_output(
                    # wmic 在 Windows 11 已废弃，改用 PowerShell/CIM 命令
                    'powershell -Command "(Get-CimInstance -Class Win32_ComputerSystemProduct).UUID"',
                    shell=True
                ).decode()
                fingerprint = output.strip()
            except Exception:
                pass
        elif system == "Linux":
            for path in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
                try:
                    with open(path, "r") as f:
                        fingerprint = f.read().strip()
                    if fingerprint:
                        break
                except FileNotFoundError:
                    continue

        if not fingerprint:
            _key_derivation_failed = True
            logger.warning("Failed to derive key from machine fingerprint, falling back to hostname-based key.")
            fingerprint = platform.node() + os.path.expanduser("~")
        else:
            _key_derivation_failed = False

        # 使用 PBKDF2 替代裸 SHA-256，增加暴力破解的计算成本
        _cached_fernet_key = _derive_fernet_key(fingerprint)
        return _cached_fernet_key

def is_key_derivation_degraded() -> bool:
    return _key_derivation_failed

def get_cipher() -> Fernet:
    # get_machine_key() 现在直接返回 base64url 编码的 PBKDF2 派生密钥，
    # 无需再次 base64 编码
    return Fernet(get_machine_key())

def encrypt(text: str) -> str:
    return get_cipher().encrypt(text.encode()).decode()

def decrypt(token: str) -> str:
    return get_cipher().decrypt(token.encode()).decode()
