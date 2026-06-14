"""Platform detection, privilege elevation, and machine identification"""

import os
import sys
import platform
import subprocess
import logging

logger = logging.getLogger(__name__)

def is_windows() -> bool:
    """Check if running on Windows"""
    return sys.platform == "win32"

def is_linux() -> bool:
    """Check if running on Linux"""
    return sys.platform.startswith("linux")

def is_macos() -> bool:
    """Check if running on macOS"""
    return sys.platform == "darwin"

def get_desktop_environment() -> str:
    """Detect current Linux desktop environment

    Returns: 'gnome' | 'kde' | 'xfce' | 'unknown' | "" (non-Linux returns empty string)
    """
    if not is_linux():
        return ""

    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    session = os.environ.get("DESKTOP_SESSION", "").lower()

    if "gnome" in desktop or "gnome" in session:
        return "gnome"
    if "kde" in desktop or "plasma" in session:
        return "kde"
    if "xfce" in desktop or "xfce" in session:
        return "xfce"

    return "unknown"

def request_admin() -> bool:
    """Request administrator privileges

    Windows: Use ShellExecuteW runas to show UAC elevation dialog
    Linux: Use pkexec to show authentication dialog
    macOS: Use osascript to prompt for admin credentials

    Returns True if the elevation request was initiated (success not guaranteed)
    """
    if is_windows():
        try:
            import ctypes
            if ctypes.windll.shell32.IsUserAnAdmin():
                return True  # Already admin
            # Show UAC dialog to relaunch as admin
            # 使用 subprocess.list2cmdline 正确转义参数（处理路径中的空格）
            params = subprocess.list2cmdline(sys.argv)
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, params, None, 1
            )
            return ret > 32
        except Exception as e:
            logger.error(f"Request admin failed: {e}")
            return False
    elif is_linux():
        try:
            # Linux pkexec elevation
            subprocess.Popen(["pkexec"] + sys.argv)
            return True
        except FileNotFoundError:
            logger.error("pkexec not found, cannot elevate privileges")
            return False
        except Exception as e:
            logger.error(f"Request admin failed: {e}")
            return False
    elif is_macos():
        try:
            # macOS osascript elevation
            app_cmd = f'{sys.executable} {" ".join(sys.argv)}'
            cmd = f'do shell script "{app_cmd}" with administrator privileges'
            subprocess.Popen(["osascript", "-e", cmd])
            return True
        except FileNotFoundError:
            logger.error("osascript not found, cannot elevate privileges")
            return False
        except Exception as e:
            logger.error(f"Request admin failed: {e}")
            return False
    return False

def get_app_data_dir() -> str:
    """Get application data directory (cross-platform)

    Windows: %APPDATA%/Venlta
    Linux: ~/.config/Venlta
    macOS: ~/Library/Application Support/Venlta

    Returns:
        Application data directory path
    """
    if is_windows():
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "Venlta")
    elif is_macos():
        base = os.path.expanduser("~/Library/Application Support")
        return os.path.join(base, "Venlta")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
        return os.path.join(base, "Venlta")

def get_machine_id() -> str:
    """Get machine unique identifier (for key derivation)

    Windows: Read registry MachineGuid
    Linux: Read /etc/machine-id
    macOS: Read IOPlatformSerialNumber via ioreg

    Returns:
        Machine ID string
    """
    if is_windows():
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                return winreg.QueryValueEx(key, "MachineGuid")[0]
        except Exception:
            return platform.node()
    elif is_linux():
        for path in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
            try:
                with open(path, "r", encoding='utf-8') as f:
                    return f.read().strip()
            except FileNotFoundError:
                continue
    elif is_macos():
        # macOS: Use IOPlatformSerialNumber from ioreg
        try:
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if "IOPlatformSerialNumber" in line:
                        # Extract value from: "IOPlatformSerialNumber" = "XXXXXXXXXX"
                        parts = line.split('"')
                        if len(parts) >= 4:
                            return parts[3]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return platform.node()
