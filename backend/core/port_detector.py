import socket
import psutil
import platform
import logging

logger = logging.getLogger(__name__)

class PortDetector:
    """检测端口冲突，避免与系统已有服务冲突"""

    def is_port_in_use(self, port: int) -> bool:
        """检测指定端口是否被占用
        检测 TCP 和 UDP 两个协议上的端口冲突。
        - TCP：sing-box 默认监听 TCP（SOCKS/HTTP 代理）
        - UDP：Hysteria2/WireGuard 等协议使用 UDP 通信
        当前检测 127.0.0.1 和 0.0.0.0 上的端口冲突。
        TUN 模式下可能监听 0.0.0.0，因此需要同时检测两个地址以避免遗漏。
        
        注意：此方法存在 TOCTOU（Time-of-Check-Time-of-Use）竞态条件——
        端口在检测后、使用前可能被其他进程占用或释放。
        这是端口检测的固有限制，无法完全避免，但影响有限：
        sing-box 绑定失败时会返回错误，用户可手动更换端口。
        """
        # TCP 检测
        for addr in ('127.0.0.1', '0.0.0.0'):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind((addr, port))
                except OSError:
                    return True
        # UDP 检测（Hysteria2、WireGuard 等使用 UDP）
        for addr in ('127.0.0.1', '0.0.0.0'):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                try:
                    s.bind((addr, port))
                except OSError:
                    return True
        return False

    def get_port_process(self, port: int) -> dict | None:
        """获取占用指定端口的进程信息"""
        try:
            connections = psutil.net_connections(kind='inet')
        except psutil.AccessDenied:
            # Windows 上非管理员权限可能无法列出所有连接
            return None
        for conn in connections:
            if conn.laddr.port == port and conn.status == 'LISTEN':
                try:
                    proc = psutil.Process(conn.pid)
                    return {
                        "port": port,
                        "pid": conn.pid,
                        "process": proc.name(),
                        "cmdline": " ".join(proc.cmdline()[:3]),
                    }
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    return {
                        "port": port,
                        "pid": conn.pid,
                        "process": "unknown",
                        "cmdline": "",
                    }
        return None

    def check_ports(self, ports: list[int]) -> dict | None:
        """检测端口列表，返回第一个冲突的端口信息，无冲突返回 None"""
        for port in ports:
            if self.is_port_in_use(port):
                info = self.get_port_process(port)
                # 排除自身进程（Windows 上进程名带 .exe 后缀）
                if info and not self._is_self_process(info.get("process")):
                    return info
        return None

    def check_all_ports(self, ports: list[int]) -> list[dict]:
        """检测所有端口冲突"""
        conflicts = []
        for port in ports:
            if self.is_port_in_use(port):
                info = self.get_port_process(port)
                if info and not self._is_self_process(info.get("process")):
                    conflicts.append(info)
        return conflicts

    @staticmethod
    def _is_self_process(proc_name: str | None) -> bool:
        """判断进程名是否为 sing-box 或 venlta 自身（兼容 Windows .exe 后缀）"""
        if not proc_name:
            return False
        name = proc_name.lower()
        # Windows: psutil 返回 "sing-box.exe" / "venlta.exe"
        # Linux/macOS: psutil 返回 "sing-box" / "venlta"
        return name in ("sing-box", "sing-box.exe", "venlta", "venlta.exe")

    def find_available_port(self, start: int, max_tries: int = 100) -> int:
        """从 start 端口开始寻找一个可用端口"""
        for port in range(start, start + max_tries):
            if not self.is_port_in_use(port):
                return port
        raise RuntimeError(f"No available port found in range {start}-{start + max_tries}")
