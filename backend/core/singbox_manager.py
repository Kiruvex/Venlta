import os
import signal
import subprocess
import threading
import time
import httpx
import logging
import platform
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Slot, QThread, QMetaObject, Qt
from core.config_manager import PROXY_SELECTOR_TAG
from core.tun_elevator import TunElevator
from utils.constants import get_data_dir

logger = logging.getLogger(__name__)

# Crash restart configuration
MAX_CRASH_RESTART = 3
CRASH_WINDOW_SECONDS = 60
RESTART_BACKOFF_BASE = 3  # 基础等待秒数，3^crash_count 递增，给端口释放更多时间


class SingboxWorker(QThread):
    """sing-box management Worker, runs in a dedicated QThread"""
    stateChanged = Signal(dict)
    logEmitted = Signal(str)
    latencyResult = Signal(dict)
    latencyRequested = Signal(list)
    closeConnectionRequested = Signal(str)
    connectionClosed = Signal(dict)

    def __init__(self, config_mgr, clash_api_secret: str = "", tun_elevator: TunElevator | None = None):
        super().__init__()
        self.config_mgr = config_mgr
        self._clash_api_secret = clash_api_secret
        self.tun_elevator = tun_elevator or TunElevator()
        self.process: subprocess.Popen | None = None
        self._crash_times: list[float] = []
        self._running = False
        self._log_threads: list[threading.Thread] = []
        # Cache current mode/node to avoid HTTP requests on every get_state()
        self._cached_mode: str | None = None
        self._cached_node: str | None = None
        # Persistent HTTP client for connection reuse
        self._clash_client: httpx.Client | None = None
        # Whether sing-box is running with elevated privileges
        self._elevated = False
        # Elevated process info dict (from TunElevator.launch_elevated())
        # Used when sing-box is launched via ShellExecuteExW (Windows UAC) or osascript (macOS)
        self._elevated_process_info: dict | None = None
        # Log file path for elevated processes that can't pipe stdout/stderr
        self._elevated_log_file: str | None = None
        # Log file reader thread for elevated processes
        self._log_file_thread: threading.Thread | None = None

    def _get_clash_client(self) -> httpx.Client:
        """Get or create persistent Clash API HTTP client"""
        if self._clash_client is None or self._clash_client.is_closed:
            self._clash_client = httpx.Client(
                base_url=f"http://127.0.0.1:{self.config_mgr.get_clash_api_port()}",
                headers=self._get_clash_api_headers(),
                timeout=5,
            )
        return self._clash_client

    def _get_clash_api_headers(self) -> dict:
        headers = {}
        if self._clash_api_secret:
            headers["Authorization"] = f"Bearer {self._clash_api_secret}"
        return headers

    def get_state(self) -> dict:
        last_crash = self._crash_times[-1] if self._crash_times else None
        is_running = self._is_process_running()
        if is_running:
            if self._cached_mode is None:
                current_mode = "route"
                QMetaObject.invokeMethod(self, "_refresh_state_async", Qt.QueuedConnection)
            else:
                current_mode = self._cached_mode
            if self._cached_node is None:
                current_node = None
                if self._cached_mode is not None:
                    QMetaObject.invokeMethod(self, "_refresh_state_async", Qt.QueuedConnection)
            else:
                current_node = self._cached_node
        else:
            current_mode = "route"
            current_node = None
        # 系统代理状态从 DB 读取（与 TUN 完全独立）
        try:
            sys_proxy_enabled = self.config_mgr.db.get_setting('system_proxy_enabled', False)
        except Exception:
            sys_proxy_enabled = False
        return {
            "isRunning": is_running,
            "currentMode": current_mode,
            "isTunEnabled": self.config_mgr.get_tun_enabled(),
            "isSystemProxyEnabled": sys_proxy_enabled,
            "currentNode": current_node,
            "currentSelectorTag": PROXY_SELECTOR_TAG,
            "restartCount": len(self._crash_times),
            "lastCrashTime": datetime.fromtimestamp(last_crash).isoformat() if last_crash else None,
        }.copy()

    def _is_process_running(self) -> bool:
        """Check if sing-box process is running, handling both subprocess and elevated modes"""
        if self._elevated_process_info:
            # Elevated process (Windows UAC / macOS osascript)
            return self.tun_elevator.is_elevated_process_running(self._elevated_process_info)
        # Standard subprocess
        return self.process is not None and self.process.poll() is None

    @Slot()
    def start_singbox(self):
        try:
            if self._is_process_running():
                logger.info("sing-box already running, skip start")
                return

            # Check crash count
            now = time.time()
            self._crash_times = [t for t in self._crash_times if now - t < CRASH_WINDOW_SECONDS]
            if len(self._crash_times) >= MAX_CRASH_RESTART:
                self.logEmitted.emit(f"[FATAL] sing-box crashed {MAX_CRASH_RESTART} times within {CRASH_WINDOW_SECONDS}s, giving up auto-restart. Please check your config.")
                self.stateChanged.emit(self.get_state())
                return

            # ★ 启动前清理残留 sing-box 进程 ★
            # 上次崩溃后可能遗留僵尸 sing-box 进程占用端口，
            # 导致新进程无法绑定端口而立即退出
            self._kill_zombie_singbox_processes()

            # ★ 端口可用性检查 ★
            # 检查 mixed inbound 端口和 Clash API 端口是否可用
            self._check_ports_available()

            # Generate sing-box config
            # skip_validate=True: 避免 fork 子进程（sing-box check）导致 glibc 堆损坏
            # 主线程的 toggleTun→regenerate() 已完成验证，此处无需重复验证
            # write_config() 内部会检查距上次 regenerate 的时间，超过 5 秒则强制验证
            config_path = self.config_mgr.write_config(skip_validate=True)
            tun_enabled = self.config_mgr.get_tun_enabled()
            method = self.tun_elevator.get_elevation_method() if tun_enabled else "none"

            logger.info(f"Starting sing-box: tun_enabled={tun_enabled}, elevation_method={method}, platform={platform.system()}")
            if tun_enabled:
                can_tun = self.tun_elevator.can_create_tun()
                logger.info(f"TUN capability check: can_create_tun={can_tun}, method={method}")
                # ★ TUN 设备清理 ★
                # 上次 sing-box 崩溃可能遗留 TUN 虚拟网卡和路由表条目，
                # 导致新的 sing-box 实例无法创建同名 TUN 设备而崩溃。
                # 参考 NekoBox：CoreProcess 在停止时清理 TUN 设备，
                # 但崩溃时无法执行清理逻辑，因此需要在启动前主动清理。
                self._cleanup_tun_device()

            # Determine launch strategy based on platform and elevation method
            system = platform.system()

            if tun_enabled and method == "uac" and system == "Windows":
                # Windows UAC: Must use launch_elevated() because subprocess.Popen
                # cannot trigger UAC elevation. ShellExecuteExW("runas") is required.
                self._start_elevated(config_path)
            elif tun_enabled and method == "osascript" and system == "Darwin":
                # macOS osascript: Use launch_elevated() for admin credential prompt
                self._start_elevated(config_path)
            else:
                # Linux (setcap/pkexec) or already-admin Windows/macOS: Use subprocess.Popen
                self._start_subprocess(config_path, tun_enabled)

            self.stateChanged.emit(self.get_state())
        except Exception as e:
            logger.error(f"Failed to start sing-box: {e}")
            self.stateChanged.emit(self.get_state())
            self.logEmitted.emit(f"[ERROR] Failed to start sing-box: {e}")

    def _start_subprocess(self, config_path, tun_enabled: bool):
        """Start sing-box via subprocess.Popen (Linux pkexec / already-admin Windows/macOS)"""
        cmd = self.tun_elevator.get_start_cmd(config_path, tun_enabled)
        if cmd is None:
            self.logEmitted.emit("[ERROR] Cannot determine sing-box startup command")
            logger.error("get_start_cmd returned None")
            return

        # Record whether starting with elevated privileges (pkexec)
        self._elevated = tun_enabled and len(cmd) > 0 and cmd[0] == "pkexec"

        if self._elevated:
            self.logEmitted.emit("[INFO] TUN mode: starting sing-box with elevated privileges (pkexec)")
            logger.info("Starting sing-box via pkexec for TUN mode")
        elif tun_enabled:
            self.logEmitted.emit("[INFO] TUN mode: starting sing-box (capability already granted)")
            logger.info("Starting sing-box for TUN mode (no pkexec needed, capability already granted)")
        else:
            logger.info(f"Starting sing-box without TUN: {' '.join(cmd[:3])}...")

        # ★ Linux: 使用 close_fds=False 避免 fork+exec 回退到 fork() ★
        # Python 3.12+ subprocess.Popen 默认使用 posix_spawn，但某些条件下
        # （如 text=True + close_fds=True）会回退到 fork()+exec()，
        # 在多线程 Qt 应用中可能导致 glibc 堆损坏崩溃。
        # 设置 close_fds=False 可以确保使用 posix_spawn。
        # 参考 NekoBox：使用 QProcess::start()（内部也是 posix_spawn），不存在此问题。
        # Windows: close_fds=True 防止子进程继承不需要的文件句柄（如 SQLite 锁），
        # 避免文件锁定问题。Windows 没有 fork，不需要 close_fds=False。
        self.process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=(platform.system() != "Linux"),
        )
        self._running = True
        self._start_log_reader()
        self._start_watchdog()

    def _start_elevated(self, config_path):
        """Start sing-box with elevated privileges via TunElevator.launch_elevated()

        Used on Windows (UAC) and macOS (osascript) where subprocess.Popen
        cannot trigger the required privilege elevation.
        """
        result = self.tun_elevator.launch_elevated(config_path)

        if not result.get("ok"):
            error = result.get("error", "Unknown error")
            self.logEmitted.emit(f"[ERROR] Failed to launch elevated sing-box: {error}")
            logger.error(f"launch_elevated failed: {error}")
            return

        self._elevated = True
        self._elevated_process_info = result
        self._running = True

        launch_method = result.get("method", "unknown")
        pid = result.get("pid", "unknown")
        self.logEmitted.emit(f"[INFO] TUN mode: starting sing-box with elevated privileges ({launch_method}, PID={pid})")
        logger.info(f"Started elevated sing-box via {launch_method}, PID={pid}")

        if launch_method == "subprocess" and result.get("process"):
            # Linux pkexec via launch_elevated() - has subprocess with stdout/stderr pipes
            self.process = result["process"]
            self._start_log_reader()
        elif launch_method == "shell_execute_ex":
            # Windows UAC - no stdout/stderr pipes available
            # Configure sing-box to log to file for log capture
            self._start_elevated_log_monitor()
        elif launch_method == "osascript":
            # macOS osascript - may have log file
            log_file = result.get("log_file")
            if log_file:
                self._elevated_log_file = log_file
                self._start_log_file_reader(log_file)
            elif result.get("process"):
                self.process = result["process"]
                self._start_log_reader()

        self._start_watchdog()

    def _start_elevated_log_monitor(self):
        """Start log monitoring for Windows elevated sing-box

        Since ShellExecuteExW doesn't provide stdout/stderr pipes,
        we configure sing-box to log to a file and tail it.
        The log file path is embedded in the config by ConfigManager
        when TUN mode is active on Windows.
        """
        # Backward compat: use legacy ~/.venlta if it exists, otherwise platform data dir
        legacy_dir = Path.home() / ".venlta"
        data_dir = legacy_dir if legacy_dir.exists() else Path(get_data_dir())
        log_file = str(data_dir / "sing-box.log")
        self._elevated_log_file = log_file
        self._start_log_file_reader(log_file)

    def _start_log_file_reader(self, log_file: str):
        """Read logs from a file (used for elevated processes without pipe access)"""

        def read_log_file():
            # Wait for the log file to be created
            for _ in range(30):
                if not self._running:
                    return
                if os.path.exists(log_file):
                    break
                time.sleep(0.5)
            else:
                self.logEmitted.emit(f"[WARN] Log file not created: {log_file}")
                return

            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    # Seek to end first to only read new lines
                    f.seek(0, 2)
                    while self._running:
                        line = f.readline()
                        if line:
                            self.logEmitted.emit(line.strip())
                        else:
                            time.sleep(0.1)
            except Exception as e:
                logger.debug(f"Log file reader stopped: {e}")

        t = threading.Thread(target=read_log_file, daemon=True)
        t.start()
        self._log_file_thread = t

    @Slot()
    def stop_singbox(self):
        self._running = False
        self._cached_mode = None
        self._cached_node = None

        if self._elevated_process_info:
            # Elevated process (Windows UAC / macOS osascript)
            self._stop_elevated()
        elif self.process:
            # Standard subprocess
            self._stop_subprocess()

        # Wait for log threads
        for t in self._log_threads:
            t.join(timeout=2)
        self._log_threads = []

        # Wait for log file reader thread
        if self._log_file_thread:
            self._log_file_thread.join(timeout=2)
            self._log_file_thread = None

        # Clean up log file
        if self._elevated_log_file:
            try:
                if os.path.exists(self._elevated_log_file):
                    os.unlink(self._elevated_log_file)
            except Exception:
                pass
            self._elevated_log_file = None

        # Clean up macOS helper files
        if self._elevated_process_info:
            pid_file = self._elevated_process_info.get("pid_file")
            if pid_file:
                try:
                    if os.path.exists(pid_file):
                        os.unlink(pid_file)
                except Exception:
                    pass

        self._elevated_process_info = None
        self._elevated = False

        # 清理 TUN 设备（参考 NekoBox CoreProcess::onExited）
        # 正常停止时 sing-box 会自行清理 TUN 设备，但为防止异常情况，
        # 在停止后也执行一次清理检查
        was_tun = self.config_mgr.get_tun_enabled() if self.config_mgr else False
        if was_tun:
            self._cleanup_tun_device()

        # Close persistent Clash API client
        if self._clash_client and not self._clash_client.is_closed:
            try:
                self._clash_client.close()
            except Exception:
                pass
            self._clash_client = None

        self.stateChanged.emit(self.get_state())

    def _stop_subprocess(self):
        """Stop a standard subprocess.Popen sing-box process

        Linux: terminate() 发送 SIGTERM，允许 sing-box 优雅清理（TUN 设备、路由表等）。
        Windows: terminate() 调用 TerminateProcess()（硬杀），等价于 SIGKILL。
        Windows 上系统代理由 system_proxy.py 单独清理，不受进程终止方式影响。
        """
        if not self.process:
            return

        try:
            self.process.stdin.close()
        except Exception:
            pass

        # pkexec forwards SIGTERM to its child process
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                self.process.kill()
            except PermissionError:
                logger.warning("Cannot kill elevated sing-box process (permission denied). "
                               "It may exit on its own or require manual cleanup.")

        self.process = None

    def _stop_elevated(self):
        """Stop an elevated sing-box process via TunElevator"""
        if not self._elevated_process_info:
            return

        result = self.tun_elevator.stop_elevated_process(self._elevated_process_info)
        if not result.get("ok"):
            logger.warning(f"Failed to stop elevated process: {result.get('error')}")

        # Also try to stop the subprocess if it exists (osascript helper process)
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

    @Slot()
    def restart_singbox(self):
        self.stop_singbox()
        crash_count = len(self._crash_times)
        backoff = min(RESTART_BACKOFF_BASE ** crash_count, 30)
        # Use msleep instead of time.sleep to avoid blocking QThread event loop
        self.msleep(int(backoff * 1000))
        self.start_singbox()

    def _start_log_reader(self):
        # 收集最后的 stderr 输出（崩溃诊断用）
        self._last_stderr_lines: list[str] = []

        def read_output(pipe, is_stderr: bool = False):
            # 二进制模式读取（Linux close_fds=False 不兼容 text=True）
            for line in iter(pipe.readline, b''):
                if line:
                    try:
                        decoded = line.decode(errors='replace').strip()
                    except Exception:
                        decoded = str(line.strip())
                    # ★ 同时输出到 Python logger，确保终端也能看到 sing-box 的输出 ★
                    # 之前只 emit 到前端，终端看不到 sing-box 的错误信息，
                    # 导致 sing-box 崩溃时无法从终端日志诊断原因
                    if is_stderr:
                        logger.warning(f"[sing-box] {decoded}")
                        # 保留最后 20 行 stderr，崩溃时输出
                        self._last_stderr_lines.append(decoded)
                        if len(self._last_stderr_lines) > 20:
                            self._last_stderr_lines.pop(0)
                    else:
                        logger.info(f"[sing-box] {decoded}")
                    self.logEmitted.emit(decoded)
        t1 = threading.Thread(target=read_output, args=(self.process.stdout, False), daemon=True)
        t2 = threading.Thread(target=read_output, args=(self.process.stderr, True), daemon=True)
        t1.start()
        t2.start()
        self._log_threads = [t1, t2]

    def _start_watchdog(self):
        def monitor():
            while self._running:
                if not self._is_process_running():
                    break
                time.sleep(1)

            if self._running:
                # Process exited unexpectedly
                # ★ 等待 stderr 线程读取完毕（最多 500ms）★
                # sing-box 崩溃后 stderr 可能还有未读取的输出，
                # 等待一小段时间确保所有错误信息都被捕获
                time.sleep(0.5)

                exit_code = self._get_process_exit_code()
                tun_enabled = self.config_mgr.get_tun_enabled() if self.config_mgr else False

                # ★ 输出崩溃前的 stderr 内容 ★
                # 这是诊断 sing-box 崩溃原因的关键信息
                if hasattr(self, '_last_stderr_lines') and self._last_stderr_lines:
                    logger.error(f"sing-box crashed! Last stderr output:")
                    for line in self._last_stderr_lines:
                        logger.error(f"  >> {line}")
                    # 尝试读取进程剩余的 stderr（如果还有未读取的数据）
                    if self.process and self.process.stderr:
                        try:
                            remaining = self.process.stderr.read()
                            if remaining:
                                for line in remaining.decode(errors='replace').strip().split('\n'):
                                    if line.strip():
                                        logger.error(f"  >> {line.strip()}")
                        except Exception:
                            pass

                if exit_code is not None and exit_code != 0:
                    self._crash_times.append(time.time())
                    signal_name = ""
                    if platform.system() == "Windows":
                        # Windows 退出码：NTSTATUS 值（正数），不是 Unix 信号取反
                        if exit_code == 0xC0000005:
                            signal_name = " (ACCESS_VIOLATION - Windows equivalent of SIGSEGV)"
                        elif exit_code == 0xC0000409:
                            signal_name = " (STACK_BUFFER_OVERRUN - Windows equivalent of SIGABRT)"
                        elif exit_code == 0xC0000008:
                            signal_name = " (INVALID_HANDLE)"
                        elif exit_code == 0xC000001D:
                            signal_name = " (ILLEGAL_INSTRUCTION)"
                    else:
                        # Linux/macOS: 退出码 = -(信号号)
                        if exit_code == -6:
                            signal_name = " (SIGABRT - likely heap corruption)"
                        elif exit_code == -11:
                            signal_name = " (SIGSEGV - segmentation fault)"
                        elif exit_code == -134:
                            signal_name = " (SIGABRT - abort())"
                    crash_msg = (
                        f"[ERROR] sing-box crashed with code {exit_code}{signal_name}, "
                        f"TUN={'ON' if tun_enabled else 'OFF'}, "
                        f"attempting restart ({len(self._crash_times)}/{MAX_CRASH_RESTART})..."
                    )
                    self.logEmitted.emit(crash_msg)
                    logger.error(crash_msg)
                    # ★ 输出配置文件路径，方便手动调试 ★
                    if self.config_mgr and self.config_mgr.config_path:
                        logger.error(f"Config file: {self.config_mgr.config_path} "
                                     f"(run 'sing-box run -c {self.config_mgr.config_path}' to debug manually)")

                    # TUN 模式下崩溃时清理残留 TUN 设备，防止重启后再次崩溃
                    if tun_enabled:
                        logger.info("TUN mode crash detected, cleaning up TUN device before restart")
                        self._cleanup_tun_device()

                    # Clean up process state before restart
                    self._cleanup_dead_process()
                    from PySide6.QtCore import QMetaObject, Qt
                    QMetaObject.invokeMethod(self, "restart_singbox", Qt.QueuedConnection)

        threading.Thread(target=monitor, daemon=True).start()

    def _get_process_exit_code(self) -> int | None:
        """Get exit code of the sing-box process (works for both subprocess and elevated)"""
        if self._elevated_process_info:
            return self.tun_elevator.get_elevated_process_exit_code(self._elevated_process_info)
        if self.process:
            return self.process.returncode
        return None

    def _cleanup_dead_process(self):
        """Clean up process state after process has exited"""
        self.process = None
        self._elevated_process_info = None
        self._elevated = False

    def _cleanup_tun_device(self):
        """清理上次 sing-box 崩溃遗留的 TUN 虚拟网卡和路由表条目

        当 sing-box 在 TUN 模式下崩溃（如 heap corruption、SIGABRT），
        TUN 虚拟网卡和路由表条目不会被自动清理。如果直接重新启动 sing-box，
        新实例尝试创建同名 TUN 设备会失败（设备已存在），导致启动失败或崩溃。

        参考 NekoBox：CoreProcess::onExited() 在进程退出时清理 TUN 设备，
        但崩溃时无法执行清理逻辑。NekoBox 的做法是在启动前检查并清理残留设备。

        此方法仅在 Linux 上执行清理（Windows/macOS 由系统自动清理）。
        """
        if platform.system() != "Linux":
            return

        try:
            # 从 DB 读取 TUN 接口名（与 ConfigManager 一致）
            tun_ifname = self.config_mgr.db.get_setting("tun_interface_name")
            if not tun_ifname:
                logger.debug("No TUN interface name configured, skip cleanup")
                return

            # 检查 TUN 设备是否存在
            result = subprocess.run(
                ["ip", "link", "show", tun_ifname],
                capture_output=True, timeout=3,
                close_fds=(platform.system() != "Linux"),
            )
            if result.returncode != 0:
                # 设备不存在，无需清理
                logger.debug(f"TUN device {tun_ifname} does not exist, skip cleanup")
                return

            # TUN 设备存在（上次崩溃遗留），需要清理
            logger.warning(f"TUN device {tun_ifname} exists (leftover from crash), cleaning up...")

            # 1. 删除 TUN 设备（同时清理关联的路由表条目）
            result = subprocess.run(
                ["ip", "link", "del", tun_ifname],
                capture_output=True, timeout=5,
                close_fds=(platform.system() != "Linux"),
            )
            if result.returncode == 0:
                logger.info(f"Successfully cleaned up leftover TUN device {tun_ifname}")
            else:
                stderr = result.stderr.decode(errors='replace').strip()
                logger.warning(f"Failed to delete TUN device {tun_ifname}: {stderr}")

        except FileNotFoundError:
            # ip 命令不存在（不太可能在 Linux 上）
            logger.debug("ip command not found, skip TUN cleanup")
        except subprocess.TimeoutExpired:
            logger.warning("TUN cleanup timed out")
        except Exception as e:
            logger.warning(f"TUN cleanup error (non-fatal): {e}")

    def _kill_zombie_singbox_processes(self):
        """清理残留的 sing-box 僵尸进程

        当 Venlta 崩溃或强制退出时，sing-box 子进程可能继续运行，
        占用 mixed inbound 端口和 Clash API 端口。
        新的 sing-box 实例无法绑定这些端口，导致启动立即失败。

        此方法查找并终止所有非当前进程的 sing-box 实例。
        """
        system = platform.system()

        # Windows: use taskkill to kill zombie sing-box processes
        if system == "Windows":
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/IM", "sing-box.exe"],
                    capture_output=True, timeout=5,
                )
                if result.returncode == 0:
                    logger.info("Zombie sing-box.exe processes cleaned up via taskkill")
                else:
                    logger.debug("No zombie sing-box.exe processes found")
            except FileNotFoundError:
                logger.debug("taskkill command not found, skip zombie process cleanup")
            except subprocess.TimeoutExpired:
                logger.warning("Zombie process check timed out")
            except Exception as e:
                logger.warning(f"Zombie process cleanup error (non-fatal): {e}")
            return

        if system != "Linux":
            return

        try:
            # 使用 pkill 查找并终止 sing-box 进程
            # --exact 精确匹配进程名，避免误杀包含 "sing-box" 的其他进程
            # --newest 保留最新的进程（即刚启动的），仅杀旧进程
            # 但更安全的做法是：杀掉所有 sing-box 进程，然后重新启动
            result = subprocess.run(
                ["pgrep", "-x", "sing-box"],
                capture_output=True, timeout=3, close_fds=(platform.system() != "Linux"),
            )
            if result.returncode != 0:
                # 没有找到 sing-box 进程
                return

            pids = result.stdout.decode(errors='replace').strip().split('\n')
            pids = [p.strip() for p in pids if p.strip()]

            if not pids:
                return

            # 排除当前进程（如果有）
            current_pid = str(self.process.pid) if self.process and self.process.poll() is None else None
            zombie_pids = [p for p in pids if p != current_pid]

            if not zombie_pids:
                return

            logger.warning(f"Found {len(zombie_pids)} zombie sing-box process(es): {zombie_pids}, killing...")

            # 发送 SIGTERM 让进程优雅退出（仅 Unix — Windows 已在上方 return）
            for pid in zombie_pids:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, ValueError):
                    pass

            # 等待进程退出（最多 3 秒）
            time.sleep(1)

            # 检查是否还有存活的进程，如果有则 SIGKILL（仅 Unix）
            for pid in zombie_pids:
                try:
                    os.kill(int(pid), 0)  # 检查进程是否还在
                    # 进程还在，发送 SIGKILL
                    logger.warning(f"Process {pid} did not exit gracefully, sending SIGKILL")
                    os.kill(int(pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, ValueError):
                    pass  # 进程已退出

            logger.info("Zombie sing-box processes cleaned up")

        except FileNotFoundError:
            # pgrep 命令不存在
            logger.debug("pgrep command not found, skip zombie process cleanup")
        except subprocess.TimeoutExpired:
            logger.warning("Zombie process check timed out")
        except Exception as e:
            logger.warning(f"Zombie process cleanup error (non-fatal): {e}")

    def _check_ports_available(self):
        """检查 sing-box 需要的端口是否可用

        如果端口被占用，sing-box 会立即崩溃（exit code 1），
        但不会给出有用的错误信息。提前检查可以：
        1. 给出明确的错误提示
        2. 避免无意义的崩溃-重启循环
        """
        import socket
        ports = self.config_mgr.get_used_ports()
        for port in ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', port))
                sock.close()
                if result == 0:
                    # 端口被占用
                    if platform.system() == "Windows":
                        diag_cmd = f"netstat -ano | findstr :{port}"
                    else:
                        diag_cmd = f"lsof -i :{port} or fuser {port}/tcp"
                    logger.error(f"Port {port} is already in use! sing-box cannot start. "
                                 f"Try: {diag_cmd}")
                    self.logEmitted.emit(
                        f"[ERROR] Port {port} is already in use! sing-box cannot bind to it. "
                        f"Please stop the process using port {port} or change the port in settings. "
                        f"Diagnostic: {diag_cmd}"
                    )
            except Exception:
                pass  # 检查失败不影响启动流程

    def _get_current_mode(self) -> str:
        try:
            resp = self._get_clash_client().get("/configs", timeout=1)
            if resp.status_code == 200:
                mode = resp.json().get("mode", "rule")
                return "route" if mode == "rule" else mode
        except Exception:
            pass
        return "route"

    def _get_current_node(self) -> str | None:
        """Get the currently selected node name from the selector group"""
        try:
            from urllib.parse import quote
            resp = self._get_clash_client().get(f"/proxies/{quote(PROXY_SELECTOR_TAG, safe='')}", timeout=1)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("now", None)
        except Exception:
            pass
        return None

    def test_latency(self, node_tags: list):
        """Test node latency via Clash API proxy tag list"""
        import concurrent.futures
        from urllib.parse import quote
        client = self._get_clash_client()
        def test_one(tag: str):
            try:
                resp = client.get(
                    f"/proxies/{quote(tag, safe='')}/delay",
                    params={"timeout": 3000, "url": "https://www.gstatic.com/generate_204"},
                    timeout=5,
                )
                if resp.status_code == 200:
                    return {"nodeId": tag, "latency": resp.json().get("delay", -1)}
                return {"nodeId": tag, "latency": -1, "error": resp.text}
            except Exception as e:
                return {"nodeId": tag, "latency": -1, "error": str(e)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(test_one, node_tags))
        self.latencyResult.emit({"results": results})

    def do_close_connection(self, conn_id: str):
        """Close a connection via Clash API (in worker thread, non-blocking)"""
        try:
            from urllib.parse import quote
            clash_api_port = self.config_mgr.get_clash_api_port()
            headers = self._get_clash_api_headers()
            resp = httpx.delete(
                f"http://127.0.0.1:{clash_api_port}/connections/{quote(conn_id, safe='')}",
                timeout=3,
                headers=headers,
            )
            if resp.status_code in (200, 204):
                self.connectionClosed.emit({"ok": True, "connId": conn_id})
            else:
                self.connectionClosed.emit({"ok": False, "connId": conn_id, "error": f"Clash API returned {resp.status_code}"})
        except Exception as e:
            self.connectionClosed.emit({"ok": False, "connId": conn_id, "error": str(e)})

    def run(self):
        """QThread entry point, keep event loop"""
        self.exec()

    # ---------- Slot methods for SingboxManager cross-thread calls ----------

    @Slot()
    def _refresh_state_async(self):
        """Asynchronously refresh cached mode/node (in worker thread)"""
        new_mode = self._get_current_mode()
        new_node = self._get_current_node()
        mode_changed = self._cached_mode != new_mode
        node_changed = self._cached_node != new_node
        self._cached_mode = new_mode
        self._cached_node = new_node
        if mode_changed or node_changed:
            self.stateChanged.emit(self.get_state())

    @Slot()
    def do_switch_mode(self):
        """Execute Clash API mode switch in worker thread"""
        current_seq = getattr(self, '_pending_switch_seq', 0)
        api_mode = getattr(self, '_pending_switch_mode', 'rule')
        try:
            clash_api_port = self.config_mgr.get_clash_api_port()
            headers = self._get_clash_api_headers()
            resp = httpx.patch(
                f"http://127.0.0.1:{clash_api_port}/configs",
                json={"mode": api_mode},
                timeout=3,
                headers=headers,
            )
            if getattr(self, '_pending_switch_seq', 0) != current_seq:
                return
            if resp.status_code == 204:
                self._cached_mode = "route" if api_mode == "rule" else api_mode
            else:
                self._cached_mode = None
        except Exception:
            self._cached_mode = None
        self.stateChanged.emit(self.get_state())

    @Slot()
    def do_switch_node(self):
        """Execute Clash API node switch in worker thread"""
        from urllib.parse import quote
        current_seq = getattr(self, '_pending_switch_node_seq', 0)
        pending = getattr(self, '_pending_switch_node', (None, None))
        group_tag, node_tag = pending
        if not group_tag or not node_tag:
            self.stateChanged.emit(self.get_state())
            return
        try:
            clash_api_port = self.config_mgr.get_clash_api_port()
            headers = self._get_clash_api_headers()
            resp = httpx.put(
                f"http://127.0.0.1:{clash_api_port}/proxies/{quote(group_tag, safe='')}",
                json={"name": node_tag},
                timeout=3,
                headers=headers,
            )
            if getattr(self, '_pending_switch_node_seq', 0) != current_seq:
                return
            if resp.status_code == 204:
                self._cached_node = node_tag
            else:
                self._cached_node = None
        except Exception:
            self._cached_node = None
        self.stateChanged.emit(self.get_state())


class SingboxManager(QObject):
    """sing-box manager, exposes synchronous interface, delegates to QThread Worker"""

    stateChanged = Signal(dict)
    logEmitted = Signal(str)
    latencyResult = Signal(dict)
    connectionClosed = Signal(dict)

    def __init__(self, config_mgr, clash_api_secret: str = "", tun_elevator: TunElevator | None = None):
        super().__init__()
        self.config_mgr = config_mgr
        self.workerThread = QThread()
        self.worker = SingboxWorker(config_mgr, clash_api_secret, tun_elevator)
        self.worker.moveToThread(self.workerThread)

        # Cross-thread signal forwarding
        self.worker.stateChanged.connect(self._on_state_changed)
        self.worker.logEmitted.connect(self.logEmitted.emit)
        self.worker.latencyResult.connect(self.latencyResult.emit)
        self.worker.connectionClosed.connect(self.connectionClosed.emit)

        # Latency test via signal (avoid Q_ARG(list) type registration issues)
        self.worker.latencyRequested.connect(self.worker.test_latency)
        # Close connection via signal (in worker thread)
        self.worker.closeConnectionRequested.connect(self.worker.do_close_connection)

        self.workerThread.started.connect(self.worker.run)
        self.workerThread.start()

        # State cache (main thread safe read)
        self._cached_state: dict = {
            "isRunning": False,
            "currentMode": "route",
            "isTunEnabled": False,
            "isSystemProxyEnabled": False,
            "currentNode": None,
            "currentSelectorTag": PROXY_SELECTOR_TAG,
            "restartCount": 0,
            "lastCrashTime": None,
        }

    def _on_state_changed(self, state: dict):
        """Cache worker state changes to main thread"""
        self._cached_state = state
        self.stateChanged.emit(state)

    def get_state(self) -> dict:
        """Return cached state, no cross-thread Worker property access"""
        return self._cached_state.copy()

    def update_cached_state(self, **kwargs):
        """Update specific fields in the cached state (main-thread only).

        Used by bridge methods (toggleSystemProxy, toggleTun) to immediately
        reflect DB/config changes in the cached state before emitting
        proxyStateChanged, avoiding the stale-cache problem where the worker
        hasn't emitted stateChanged yet.
        """
        self._cached_state.update(kwargs)

    def start(self):
        QMetaObject.invokeMethod(self.worker, "start_singbox", Qt.QueuedConnection)

    def stop(self):
        QMetaObject.invokeMethod(self.worker, "stop_singbox", Qt.QueuedConnection)

    def restart(self):
        QMetaObject.invokeMethod(self.worker, "restart_singbox", Qt.QueuedConnection)

    def test_latency(self, node_tags: list):
        self.worker.latencyRequested.emit(node_tags)

    def switch_mode_async(self, api_mode: str):
        """Async mode switch (non-blocking main thread)"""
        self.worker._pending_switch_mode = api_mode
        self.worker._pending_switch_seq = getattr(self.worker, '_pending_switch_seq', 0) + 1
        QMetaObject.invokeMethod(self.worker, "do_switch_mode", Qt.QueuedConnection)

    def switch_node_async(self, group_tag: str, node_tag: str):
        """Async node switch (non-blocking main thread)"""
        self.worker._pending_switch_node = (group_tag, node_tag)
        self.worker._pending_switch_node_seq = getattr(self.worker, '_pending_switch_node_seq', 0) + 1
        QMetaObject.invokeMethod(self.worker, "do_switch_node", Qt.QueuedConnection)

    def close_connection_async(self, conn_id: str):
        """Async close connection (non-blocking main thread)"""
        self.worker.closeConnectionRequested.emit(conn_id)

    def get_clash_api_headers(self) -> dict:
        """Get Clash API auth headers (public API for external modules)"""
        headers = {}
        if self.worker._clash_api_secret:
            headers["Authorization"] = f"Bearer {self.worker._clash_api_secret}"
        return headers
