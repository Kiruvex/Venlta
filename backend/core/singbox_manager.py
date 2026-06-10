import os
import subprocess
import threading
import time
import httpx
import logging
import platform
from datetime import datetime
from PySide6.QtCore import QObject, Signal, Slot, QThread, QMetaObject, Qt
from core.config_manager import PROXY_SELECTOR_TAG
from core.tun_elevator import TunElevator

logger = logging.getLogger(__name__)

# Crash restart configuration
MAX_CRASH_RESTART = 3
CRASH_WINDOW_SECONDS = 60
RESTART_BACKOFF_BASE = 2


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
        return {
            "isRunning": is_running,
            "currentMode": current_mode,
            "isTunEnabled": self.config_mgr.get_tun_enabled(),
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
                return

            # Check crash count
            now = time.time()
            self._crash_times = [t for t in self._crash_times if now - t < CRASH_WINDOW_SECONDS]
            if len(self._crash_times) >= MAX_CRASH_RESTART:
                self.logEmitted.emit(f"[FATAL] sing-box crashed {MAX_CRASH_RESTART} times within {CRASH_WINDOW_SECONDS}s, giving up auto-restart. Please check your config.")
                self.stateChanged.emit(self.get_state())
                return

            # Generate sing-box config
            config_path = self.config_mgr.write_config()
            tun_enabled = self.config_mgr.get_tun_enabled()
            method = self.tun_elevator.get_elevation_method() if tun_enabled else "none"

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

        self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
        log_file = os.path.join(os.path.expanduser("~"), ".venlta", "sing-box.log")
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

        # Close persistent Clash API client
        if self._clash_client and not self._clash_client.is_closed:
            try:
                self._clash_client.close()
            except Exception:
                pass
            self._clash_client = None

        self.stateChanged.emit(self.get_state())

    def _stop_subprocess(self):
        """Stop a standard subprocess.Popen sing-box process"""
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
        def read_output(pipe):
            for line in iter(pipe.readline, ''):
                if line:
                    self.logEmitted.emit(line.strip())
        t1 = threading.Thread(target=read_output, args=(self.process.stdout,), daemon=True)
        t2 = threading.Thread(target=read_output, args=(self.process.stderr,), daemon=True)
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
                exit_code = self._get_process_exit_code()
                if exit_code is not None and exit_code != 0:
                    self._crash_times.append(time.time())
                    self.logEmitted.emit(
                        f"[ERROR] sing-box crashed with code {exit_code}, "
                        f"attempting restart ({len(self._crash_times)}/{MAX_CRASH_RESTART})..."
                    )
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
