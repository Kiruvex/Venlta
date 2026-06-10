"""TUN privilege elevation manager

Architecture (following NekoBox):
- sing-box natively supports TUN device creation, no external helper needed
- TUN mode requires elevated privileges (NET_ADMIN on Linux, Admin on Windows, root on macOS)
- Elevation strategies are platform-specific:

  Linux:
    1. setcap (preferred): Grant cap_net_admin,cap_net_raw to sing-box binary
       via pkexec setcap. One-time authentication, sing-box runs as normal user
       with only network capabilities. Capability lost after binary update.
    2. pkexec (fallback, NekoBox approach): Start sing-box via pkexec, giving
       it full root privileges. Authentication required every time TUN starts.

  Windows:
    1. admin (preferred): App already running as administrator, sing-box inherits
       admin privileges and can create TUN devices directly.
    2. uac (fallback): Launch sing-box via ShellExecuteExW("runas") to trigger
       UAC elevation. Only sing-box is elevated, not the entire app.
       Process handle is retained for monitoring and termination.

  macOS:
    1. root (preferred): App running as root (e.g., via sudo).
    2. osascript (fallback): Use osascript to prompt for admin credentials,
       then launch sing-box via sudo. Requires password each time.

Signal forwarding:
- Linux: pkexec forwards SIGTERM/SIGINT to its child process
- Windows: ShellExecuteExW returns a process handle; TerminateProcess is used
- macOS: sudo forwards signals to its child process
"""

import os
import sys
import platform
import subprocess
import shutil
import logging
import signal
from core.config_manager import find_singbox_binary

logger = logging.getLogger(__name__)

# Linux TUN minimum capability set
# cap_net_admin: create/configure TUN device, modify routing table
# cap_net_raw: raw sockets (ICMP etc.)
_TUN_CAPABILITIES = "cap_net_admin,cap_net_raw+ep"


class TunElevator:
    """TUN privilege elevation manager

    Responsibilities:
    - Check if sing-box can create TUN devices without additional elevation
    - Determine the best elevation method per platform
    - Grant capability via pkexec setcap (one-time, Linux)
    - Provide the correct launch method for starting sing-box with TUN
    - Launch elevated sing-box on Windows via ShellExecuteExW
    - NOT responsible for sing-box lifecycle management (that's SingboxManager)
    """

    def __init__(self):
        self._capability_granted = False  # Session cache: capability granted this session

    # ---- Public API ----

    def can_create_tun(self) -> bool:
        """Check if sing-box can create TUN devices without additional elevation

        Returns:
            True if sing-box can directly create TUN devices
        """
        system = platform.system()
        if system == "Linux":
            return self._check_linux_capability()
        elif system == "Windows":
            return self._check_windows_admin()
        elif system == "Darwin":
            return self._check_macos_root()
        else:
            logger.warning(f"TUN mode not supported on {system}")
            return False

    def needs_elevation(self) -> bool:
        """Check if TUN mode requires privilege elevation"""
        return not self.can_create_tun()

    def get_elevation_method(self) -> str:
        """Determine the best available elevation method

        Returns:
            "none"       - no elevation needed (already has capability or running as root/admin)
            "setcap"     - grant capability via pkexec setcap (one-time auth, Linux preferred)
            "pkexec"     - start sing-box via pkexec (auth every time, Linux fallback)
            "uac"        - launch sing-box via UAC ShellExecuteExW (Windows)
            "osascript"  - launch sing-box via osascript/sudo (macOS)
            "unavailable" - no elevation method available on this platform
        """
        if self.can_create_tun():
            return "none"

        system = platform.system()

        if system == "Linux":
            # Linux: prefer setcap, fall back to pkexec
            if shutil.which("setcap") and shutil.which("pkexec"):
                return "setcap"
            if shutil.which("pkexec"):
                return "pkexec"
            logger.error("Neither setcap nor pkexec found, cannot elevate for TUN")
            return "unavailable"

        elif system == "Windows":
            # Windows: use UAC elevation
            return "uac"

        elif system == "Darwin":
            # macOS: use osascript for admin credentials
            return "osascript"

        return "unavailable"

    def grant_capability(self) -> dict:
        """Grant NET_ADMIN capability to sing-box binary via pkexec setcap (Linux)

        After granting, sing-box can create TUN devices as a normal user process.
        Only needs to be executed once; capability is lost when the binary is replaced
        (e.g., after an update), requiring re-grant.

        Returns:
            {"ok": bool, "error": str}
        """
        system = platform.system()
        if system != "Linux":
            return {"ok": False, "error": "Capability granting is only supported on Linux"}

        singbox_bin = find_singbox_binary()
        if not singbox_bin or singbox_bin == "sing-box":
            return {"ok": False, "error": "sing-box binary not found"}

        if not shutil.which("setcap"):
            return {"ok": False, "error": "setcap command not found. Install libcap2-bin package."}

        if not shutil.which("pkexec"):
            return {"ok": False, "error": "pkexec not found. Install polkit package."}

        try:
            result = subprocess.run(
                ["pkexec", "setcap", _TUN_CAPABILITIES, singbox_bin],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                self._capability_granted = True
                logger.info(f"Granted {_TUN_CAPABILITIES} to {singbox_bin}")
                return {"ok": True}
            else:
                error = result.stderr.strip() or f"setcap failed with code {result.returncode}"
                if result.returncode in (126, 127):
                    error = "Authentication cancelled or pkexec not available"
                logger.error(f"Failed to grant capability: {error}")
                return {"ok": False, "error": error}
        except FileNotFoundError:
            return {"ok": False, "error": "pkexec not found. Install polkit package."}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Capability grant timed out (authentication may have been cancelled)"}
        except Exception as e:
            logger.error(f"Unexpected error granting capability: {e}")
            return {"ok": False, "error": str(e)}

    def check_and_grant_capability(self) -> dict:
        """Check if capability is already granted, and auto-grant if not

        Returns:
            {"ok": bool, "error": str, "already_has": bool}
        """
        if self.can_create_tun():
            return {"ok": True, "already_has": True}

        # If already granted this session, re-check (state may have changed)
        if self._capability_granted and self.can_create_tun():
            return {"ok": True, "already_has": False}

        system = platform.system()

        if system == "Linux":
            result = self.grant_capability()
            result["already_has"] = False
            return result

        # Windows/macOS: no persistent capability to grant
        # On Windows, UAC elevation happens at process launch time
        # On macOS, credentials are provided at launch time
        method = self.get_elevation_method()
        if method == "uac":
            return {"ok": True, "already_has": False}
        elif method == "osascript":
            return {"ok": True, "already_has": False}

        return {"ok": False, "already_has": False, "error": "No elevation method available"}

    def get_start_cmd(self, config_path: str, tun_enabled: bool) -> list[str] | None:
        """Get the subprocess command for starting sing-box

        For non-TUN mode: always starts as normal user process
        For TUN mode on Linux: uses setcap or pkexec
        For TUN mode on Windows: returns base_cmd (use launch_elevated() instead)
        For TUN mode on macOS: returns sudo command via osascript

        IMPORTANT: On Windows with UAC elevation, use launch_elevated() instead
        of subprocess.Popen with this command, because UAC cannot be triggered
        via subprocess.Popen. get_start_cmd() returns the base command for
        Windows, and SingboxWorker should call launch_elevated() when the
        elevation method is "uac".

        Args:
            config_path: Path to sing-box config file
            tun_enabled: Whether TUN mode is enabled

        Returns:
            Command list for subprocess.Popen, or None on failure.
            On Windows with UAC, returns base_cmd but caller should use
            launch_elevated() instead.
        """
        singbox_bin = find_singbox_binary()
        if not singbox_bin:
            return None

        base_cmd = [singbox_bin, "run", "-c", str(config_path)]

        if not tun_enabled:
            # Non-TUN mode: always start as normal user
            return base_cmd

        # TUN mode: determine elevation method
        method = self.get_elevation_method()

        if method == "none":
            # Already has capability or running as root/admin
            return base_cmd

        elif method == "setcap":
            # Try to grant capability (one-time auth dialog)
            result = self.check_and_grant_capability()
            if result.get("ok") and result.get("already_has") is not False or result.get("ok"):
                if self.can_create_tun():
                    # Capability granted, start normally
                    return base_cmd
            # setcap failed, fall back to pkexec start
            logger.warning("setcap grant failed, falling back to pkexec start for TUN mode")
            return ["pkexec"] + base_cmd

        elif method == "pkexec":
            # NekoBox approach: start sing-box via pkexec (auth every time)
            return ["pkexec"] + base_cmd

        elif method == "uac":
            # Windows UAC: caller should use launch_elevated() instead
            # Return base_cmd as fallback (will fail without admin if TUN is needed)
            return base_cmd

        elif method == "osascript":
            # macOS: use osascript to prompt for credentials, then sudo
            # osascript -e 'do shell script "..." with administrator privileges'
            singbox_cmd = " ".join(base_cmd)
            return [
                "osascript", "-e",
                f'do shell script "{singbox_cmd}" with administrator privileges'
            ]

        logger.error(f"Unknown elevation method: {method}")
        return None

    def launch_elevated(self, config_path: str) -> dict:
        """Launch sing-box with elevated privileges (platform-specific)

        This method is used when subprocess.Popen cannot trigger the required
        elevation (e.g., Windows UAC). On Linux/macOS, it falls back to
        using subprocess.Popen with the elevated command.

        Args:
            config_path: Path to sing-box config file

        Returns:
            {
                "ok": bool,
                "method": str,  # "subprocess" | "shell_execute_ex" | "osascript"
                "process": subprocess.Popen | None,  # for subprocess method
                "handle": int | None,  # Windows process handle (shell_execute_ex)
                "pid": int | None,  # process ID
                "error": str | None,
            }
        """
        singbox_bin = find_singbox_binary()
        if not singbox_bin:
            return {"ok": False, "method": None, "error": "sing-box binary not found"}

        system = platform.system()

        if system == "Linux":
            cmd = ["pkexec", singbox_bin, "run", "-c", str(config_path)]
            try:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                return {
                    "ok": True, "method": "subprocess", "process": proc,
                    "pid": proc.pid,
                }
            except FileNotFoundError:
                return {"ok": False, "method": None, "error": "pkexec not found. Install polkit package."}
            except Exception as e:
                return {"ok": False, "method": None, "error": str(e)}

        elif system == "Windows":
            return self._launch_windows_elevated(singbox_bin, str(config_path))

        elif system == "Darwin":
            return self._launch_macos_elevated(singbox_bin, str(config_path))

        return {"ok": False, "method": None, "error": f"TUN not supported on {system}"}

    def stop_elevated_process(self, process_info: dict) -> dict:
        """Stop an elevated sing-box process

        Handles platform-specific process termination:
        - subprocess method: standard terminate/kill
        - shell_execute_ex method: TerminateProcess on handle + taskkill fallback
        - osascript method: send SIGTERM via osascript/sudo

        Args:
            process_info: The dict returned by launch_elevated()

        Returns:
            {"ok": bool, "error": str | None}
        """
        method = process_info.get("method")

        if method == "subprocess":
            proc = process_info.get("process")
            if proc is None:
                return {"ok": True}
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except PermissionError:
                    logger.warning("Cannot kill elevated process (permission denied)")
                    return {"ok": False, "error": "Cannot kill elevated process"}
            return {"ok": True}

        elif method == "shell_execute_ex":
            return self._stop_windows_elevated(
                process_info.get("handle", 0),
                process_info.get("pid", 0),
            )

        elif method == "osascript":
            pid = process_info.get("pid")
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    return {"ok": True}
                except PermissionError:
                    # Try via osascript with admin privileges
                    return self._stop_macos_elevated(pid)
                except ProcessLookupError:
                    return {"ok": True}  # Process already gone
            return {"ok": False, "error": "No PID available"}

        return {"ok": False, "error": f"Unknown process method: {method}"}

    def is_elevated_process_running(self, process_info: dict) -> bool:
        """Check if an elevated sing-box process is still running

        Args:
            process_info: The dict returned by launch_elevated()

        Returns:
            True if the process is still running
        """
        method = process_info.get("method")

        if method == "subprocess":
            proc = process_info.get("process")
            if proc is None:
                return False
            return proc.poll() is None

        elif method == "shell_execute_ex":
            return self._is_windows_process_running(process_info.get("handle", 0))

        elif method == "osascript":
            pid = process_info.get("pid")
            if not pid:
                return False
            try:
                os.kill(pid, 0)  # Check if process exists (no signal sent)
                return True
            except (ProcessLookupError, PermissionError):
                return False

        return False

    def get_elevated_process_exit_code(self, process_info: dict) -> int | None:
        """Get the exit code of an elevated sing-box process

        Returns:
            Exit code (int) if process has exited, None if still running
        """
        method = process_info.get("method")

        if method == "subprocess":
            proc = process_info.get("process")
            if proc is None:
                return -1
            return proc.returncode

        elif method == "shell_execute_ex":
            handle = process_info.get("handle", 0)
            if not handle:
                return -1
            return self._get_windows_exit_code(handle)

        return None

    def get_capability_status(self) -> dict:
        """Get detailed TUN capability status for display

        Returns:
            {"can_create_tun": bool, "platform": str, "details": str,
             "elevation_method": str}
        """
        system = platform.system()
        can = self.can_create_tun()
        details = ""
        method = self.get_elevation_method()

        if system == "Linux":
            if os.geteuid() == 0:
                details = "Running as root"
            else:
                singbox_bin = find_singbox_binary()
                if singbox_bin and singbox_bin != "sing-box":
                    try:
                        result = subprocess.run(
                            ["getcap", singbox_bin],
                            capture_output=True, text=True, timeout=5
                        )
                        if result.returncode == 0:
                            caps = result.stdout.strip()
                            details = f"Capabilities: {caps or 'none'}"
                        else:
                            details = "getcap not available"
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        details = "getcap not available"
                else:
                    details = "sing-box binary not found"
        elif system == "Windows":
            try:
                import ctypes
                is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
                details = f"Running as admin: {is_admin}"
            except Exception:
                details = "Cannot check admin status"
        elif system == "Darwin":
            if os.geteuid() == 0:
                details = "Running as root"
            else:
                details = "Standard user (elevation required for TUN)"

        return {
            "can_create_tun": can,
            "platform": system,
            "details": details,
            "elevation_method": method,
        }

    def get_elevation_error_message(self) -> str:
        """Get user-friendly elevation error message for current platform"""
        system = platform.system()
        method = self.get_elevation_method()

        if system == "Linux":
            singbox_bin = find_singbox_binary()
            if method == "setcap":
                return (
                    f"TUN mode requires elevated privileges. "
                    f"Grant NET_ADMIN capability to sing-box (recommended, one-time): "
                    f"sudo setcap {_TUN_CAPABILITIES} {singbox_bin}"
                )
            elif method == "pkexec":
                return (
                    f"TUN mode requires root privileges. "
                    f"pkexec will prompt for authentication when starting sing-box."
                )
            else:
                return (
                    f"TUN mode requires elevated privileges but no elevation method is available. "
                    f"Install polkit (pkexec) or libcap2-bin (setcap) package."
                )
        elif system == "Windows":
            return (
                "TUN mode requires administrator privileges. "
                "Venlta will prompt for UAC elevation when starting sing-box with TUN mode. "
                "Alternatively, restart Venlta as administrator."
            )
        elif system == "Darwin":
            return (
                "TUN mode requires administrator privileges on macOS. "
                "Venlta will prompt for your password when starting sing-box with TUN mode."
            )
        return "TUN mode is not supported on this platform."

    # ---- Linux ----

    def _check_linux_capability(self) -> bool:
        """Check if sing-box has NET_ADMIN capability or is running as root (Linux)"""
        # Running as root
        if os.geteuid() == 0:
            return True

        # Check file capabilities on sing-box binary
        singbox_bin = find_singbox_binary()
        if not singbox_bin or singbox_bin == "sing-box":
            return False

        try:
            result = subprocess.run(
                ["getcap", singbox_bin],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and "cap_net_admin" in result.stdout:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return False

    # ---- Windows ----

    def _check_windows_admin(self) -> bool:
        """Check if running as administrator (Windows)"""
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    def _launch_windows_elevated(self, singbox_bin: str, config_path: str) -> dict:
        """Launch sing-box with UAC elevation on Windows

        Uses ShellExecuteExW with "runas" verb and SEE_MASK_NOCLOSEPROCESS
        to obtain a process handle for monitoring and termination.

        If already running as admin, falls back to subprocess.Popen.

        Returns:
            {"ok": bool, "method": str, "handle": int, "pid": int, "error": str}
        """
        # If already admin, start normally via subprocess
        if self._check_windows_admin():
            try:
                cmd = [singbox_bin, "run", "-c", config_path]
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                return {
                    "ok": True, "method": "subprocess", "process": proc,
                    "pid": proc.pid,
                }
            except Exception as e:
                return {"ok": False, "method": None, "error": str(e)}

        # Not admin: use ShellExecuteExW to trigger UAC
        try:
            import ctypes
            from ctypes import wintypes

            class SHELLEXECUTEINFOW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("fMask", wintypes.ULONG),
                    ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", wintypes.INT),
                    ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", wintypes.LPVOID),
                    ("lpClass", wintypes.LPCWSTR),
                    ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE),
                ]

            SEE_MASK_NOCLOSEPROCESS = 0x00000040
            SW_HIDE = 0

            sei = SHELLEXECUTEINFOW()
            sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
            sei.fMask = SEE_MASK_NOCLOSEPROCESS
            sei.lpVerb = "runas"
            sei.lpFile = singbox_bin
            sei.lpParameters = f'run -c "{config_path}"'
            sei.nShow = SW_HIDE

            ret = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
            if ret:
                pid = ctypes.windll.kernel32.GetProcessId(sei.hProcess)
                logger.info(f"Launched elevated sing-box via UAC, PID={pid}")
                return {
                    "ok": True, "method": "shell_execute_ex",
                    "handle": sei.hProcess, "pid": pid,
                }
            else:
                err = ctypes.GetLastError()
                if err == 1223:  # ERROR_CANCELLED
                    return {"ok": False, "method": None, "error": "UAC elevation cancelled by user"}
                return {"ok": False, "method": None, "error": f"ShellExecuteExW failed (error {err})"}

        except Exception as e:
            logger.error(f"Failed to launch elevated sing-box on Windows: {e}")
            return {"ok": False, "method": None, "error": str(e)}

    def _stop_windows_elevated(self, handle: int, pid: int) -> dict:
        """Stop an elevated sing-box process on Windows

        Strategy:
        1. Try TerminateProcess on the handle (works if handle has PROCESS_TERMINATE access)
        2. Fallback: use taskkill with UAC elevation via ShellExecuteW
        """
        if not handle and not pid:
            return {"ok": False, "error": "No process handle or PID available"}

        # Strategy 1: TerminateProcess on the handle
        if handle:
            try:
                import ctypes
                result = ctypes.windll.kernel32.TerminateProcess(handle, 1)
                if result:
                    # Wait briefly and close handle
                    ctypes.windll.kernel32.WaitForSingleObject(handle, 3000)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    logger.info(f"Terminated elevated sing-box via handle (PID={pid})")
                    return {"ok": True}
                else:
                    err = ctypes.GetLastError()
                    logger.warning(f"TerminateProcess failed (error {err}), trying taskkill fallback")
            except Exception as e:
                logger.warning(f"TerminateProcess exception: {e}, trying taskkill fallback")

        # Strategy 2: taskkill with UAC elevation
        if pid:
            try:
                import ctypes
                ret = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", "taskkill",
                    f"/F /PID {pid}", None, 0
                )
                if ret > 32:
                    logger.info(f"Terminated elevated sing-box via taskkill (PID={pid})")
                    return {"ok": True}
                return {"ok": False, "error": f"taskkill via UAC failed (ShellExecuteW returned {ret})"}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        return {"ok": False, "error": "Failed to terminate elevated process"}

    def _is_windows_process_running(self, handle: int) -> bool:
        """Check if a Windows process is still running via its handle"""
        if not handle:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            exit_code = wintypes.DWORD()
            result = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if result:
                # STILL_ACTIVE = 259
                return exit_code.value == 259
            return False
        except Exception:
            return False

    def _get_windows_exit_code(self, handle: int) -> int | None:
        """Get exit code of a Windows process via its handle"""
        if not handle:
            return -1
        try:
            import ctypes
            from ctypes import wintypes

            exit_code = wintypes.DWORD()
            result = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if result:
                if exit_code.value == 259:  # STILL_ACTIVE
                    return None
                return exit_code.value
            return -1
        except Exception:
            return -1

    # ---- macOS ----

    def _check_macos_root(self) -> bool:
        """Check if running as root (macOS)"""
        try:
            return os.geteuid() == 0
        except Exception:
            return False

    def _launch_macos_elevated(self, singbox_bin: str, config_path: str) -> dict:
        """Launch sing-box with admin privileges on macOS

        Uses osascript to prompt for administrator credentials,
        then runs sing-box via sudo.

        If already running as root, starts normally via subprocess.Popen.

        Returns:
            {"ok": bool, "method": str, "process": subprocess.Popen, "pid": int, "error": str}
        """
        # If already root, start normally
        if self._check_macos_root():
            try:
                cmd = [singbox_bin, "run", "-c", config_path]
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                return {
                    "ok": True, "method": "subprocess", "process": proc,
                    "pid": proc.pid,
                }
            except Exception as e:
                return {"ok": False, "method": None, "error": str(e)}

        # Not root: use osascript to prompt for admin credentials
        # The "do shell script ... with administrator privileges" approach
        # runs the command as root after user authentication.
        # However, this runs in AppleScript's context and we can't easily
        # get the PID or control the process.
        #
        # Better approach: Use a helper script that starts sing-box via sudo,
        # writes its PID to a file, and forwards stdout/stderr.
        try:
            import tempfile

            # Create a helper script that starts sing-box via sudo and
            # writes its PID to a file for process management
            pid_file = os.path.join(tempfile.gettempdir(), "venlta_singbox.pid")
            log_file = os.path.join(tempfile.gettempdir(), "venlta_singbox.log")

            helper_script = f"""#!/bin/bash
{singbox_bin} run -c '{config_path}' > '{log_file}' 2>&1 &
echo $! > '{pid_file}'
wait $!
"""
            script_path = os.path.join(tempfile.gettempdir(), "venlta_singbox_launch.sh")
            with open(script_path, "w") as f:
                f.write(helper_script)
            os.chmod(script_path, 0o755)

            # Use osascript to run the helper script with admin privileges
            # This will show a macOS authentication dialog
            cmd = [
                "osascript", "-e",
                f'do shell script "bash \\"{script_path}\\"" with administrator privileges'
            ]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            # Wait briefly for the PID file to be written
            import time
            pid = None
            for _ in range(10):
                time.sleep(0.5)
                try:
                    with open(pid_file, "r") as f:
                        pid = int(f.read().strip())
                        break
                except (FileNotFoundError, ValueError):
                    continue

            if pid:
                logger.info(f"Launched elevated sing-box via osascript, PID={pid}")
                return {
                    "ok": True, "method": "osascript",
                    "process": proc, "pid": pid,
                    "log_file": log_file, "pid_file": pid_file,
                }
            else:
                # PID file not created, fall back to monitoring the osascript process
                logger.warning("Could not read sing-box PID file, using osascript process PID")
                return {
                    "ok": True, "method": "osascript",
                    "process": proc, "pid": proc.pid,
                    "log_file": log_file, "pid_file": pid_file,
                }

        except Exception as e:
            logger.error(f"Failed to launch elevated sing-box on macOS: {e}")
            return {"ok": False, "method": None, "error": str(e)}

    def _stop_macos_elevated(self, pid: int) -> dict:
        """Stop an elevated sing-box process on macOS via osascript"""
        try:
            cmd = [
                "osascript", "-e",
                f'do shell script "kill {pid}" with administrator privileges'
            ]
            subprocess.run(cmd, capture_output=True, timeout=10)
            logger.info(f"Stopped elevated sing-box via osascript kill (PID={pid})")
            return {"ok": True}
        except Exception as e:
            logger.error(f"Failed to stop elevated sing-box on macOS: {e}")
            return {"ok": False, "error": str(e)}


# Backward compatibility alias
TunHelper = TunElevator
