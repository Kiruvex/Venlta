import platform
import subprocess
import logging
import json
from pathlib import Path
from utils.constants import get_data_dir

logger = logging.getLogger(__name__)

# Windows InternetSetOption constants
INTERNET_OPTION_SETTINGS_CHANGED = 37
INTERNET_OPTION_REFRESH = 73


def _get_macos_network_services() -> list[str]:
    """Get list of macOS network services for proxy configuration

    Uses networksetup -listallnetworkservices to enumerate available
    network services (Wi-Fi, Ethernet, Thunderbolt Bridge, etc.)
    """
    try:
        result = subprocess.run(
            ["networksetup", "-listallnetworkservices"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            # First line is a header "An asterisk (*) denotes..."
            services = [line.lstrip('* ').strip() for line in lines[1:] if line.strip()]
            return services
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return []


class SystemProxy:
    def __init__(self):
        self.original_settings = None

    def set_enabled(self, enabled: bool, host="127.0.0.1", port=10809, socks_port=10808):
        try:
            if enabled:
                self._backup()
                system = platform.system()
                if system == "Windows":
                    self._set_windows_proxy(host, port)
                elif system == "Linux":
                    self._set_linux_proxy(host, port, socks_port)
                elif system == "Darwin":
                    self._set_macos_proxy(host, port, socks_port)
                else:
                    logger.warning(f"Unsupported platform: {system}")
            else:
                self._restore()
        except Exception as e:
            logger.error(f"Failed to set system proxy: {e}")

    def _backup(self):
        system = platform.system()
        try:
            if system == "Windows":
                self._backup_windows()
            elif system == "Linux":
                self._backup_linux()
            elif system == "Darwin":
                self._backup_macos()
        except Exception as e:
            logger.error(f"Failed to backup proxy settings: {e}")
            self.original_settings = None

    # ---- Windows ----
    def _backup_windows(self):
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings", 0, winreg.KEY_READ)
        try:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
            try:
                override = winreg.QueryValueEx(key, "ProxyOverride")[0]
            except FileNotFoundError:
                override = ""
            try:
                auto_config_url = winreg.QueryValueEx(key, "AutoConfigURL")[0]
            except FileNotFoundError:
                auto_config_url = ""
            self.original_settings = ("Windows", {
                "enabled": enabled, "server": server,
                "override": override, "auto_config_url": auto_config_url,
            })
        except FileNotFoundError:
            self.original_settings = ("Windows", {"enabled": 0, "server": "", "override": "", "auto_config_url": ""})
        finally:
            winreg.CloseKey(key)

    def _set_windows_proxy(self, host, port):
        import winreg
        import ctypes
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"{host}:{port}")
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "localhost;127.0.0.1;<local>")
        # Clear any PAC auto-config URL so manual proxy takes effect
        try:
            winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, "")
        except Exception:
            pass
        winreg.CloseKey(key)
        # Notify system proxy settings change
        ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)

    # ---- Linux ----
    def _backup_linux(self):
        import os
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
        if "GNOME" in desktop or "Ubuntu" in desktop:
            mode = subprocess.getoutput("gsettings get org.gnome.system.proxy mode")
            http_host = subprocess.getoutput("gsettings get org.gnome.system.proxy.http host")
            http_port = subprocess.getoutput("gsettings get org.gnome.system.proxy.http port")
            https_host = subprocess.getoutput("gsettings get org.gnome.system.proxy.https host")
            https_port = subprocess.getoutput("gsettings get org.gnome.system.proxy.https port")
            socks_host = subprocess.getoutput("gsettings get org.gnome.system.proxy.socks host")
            socks_port = subprocess.getoutput("gsettings get org.gnome.system.proxy.socks port")
            self.original_settings = ("Linux-GNOME", {
                "mode": mode, "http_host": http_host, "http_port": http_port,
                "https_host": https_host, "https_port": https_port,
                "socks_host": socks_host, "socks_port": socks_port,
            })
        elif "KDE" in desktop:
            try:
                proxy_type = subprocess.getoutput("kreadconfig6 --file kioslaverc --group 'Proxy Settings' --key ProxyType")
                http_proxy = subprocess.getoutput("kreadconfig6 --file kioslaverc --group 'Proxy Settings' --key httpProxy")
                https_proxy = subprocess.getoutput("kreadconfig6 --file kioslaverc --group 'Proxy Settings' --key httpsProxy")
                socks_proxy = subprocess.getoutput("kreadconfig6 --file kioslaverc --group 'Proxy Settings' --key socksProxy")
                self.original_settings = ("Linux-KDE", {"proxy_type": proxy_type, "http_proxy": http_proxy, "https_proxy": https_proxy, "socks_proxy": socks_proxy})
            except Exception:
                self.original_settings = ("Linux-KDE", {"desktop": desktop})
        elif "XFCE" in desktop.upper():
            try:
                http_enabled = subprocess.getoutput("xfconf-query -c xfce4-session -p /proxy/http/enabled")
                http_host = subprocess.getoutput("xfconf-query -c xfce4-session -p /proxy/http/host")
                http_port = subprocess.getoutput("xfconf-query -c xfce4-session -p /proxy/http/port")
                https_enabled = subprocess.getoutput("xfconf-query -c xfce4-session -p /proxy/https/enabled")
                https_host = subprocess.getoutput("xfconf-query -c xfce4-session -p /proxy/https/host")
                https_port = subprocess.getoutput("xfconf-query -c xfce4-session -p /proxy/https/port")
                socks_enabled = subprocess.getoutput("xfconf-query -c xfce4-session -p /proxy/socks/enabled")
                socks_host = subprocess.getoutput("xfconf-query -c xfce4-session -p /proxy/socks/host")
                socks_port = subprocess.getoutput("xfconf-query -c xfce4-session -p /proxy/socks/port")
                check_result = subprocess.run(
                    ["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/http/enabled"],
                    capture_output=True, timeout=5
                )
                if check_result.returncode != 0:
                    raise RuntimeError(f"xfconf-query failed with return code {check_result.returncode}")
                self.original_settings = ("Linux-Xfce", {"http_enabled": http_enabled, "http_host": http_host, "http_port": http_port, "https_enabled": https_enabled, "https_host": https_host, "https_port": https_port, "socks_enabled": socks_enabled, "socks_host": socks_host, "socks_port": socks_port})
            except Exception:
                self.original_settings = ("Linux-Xfce", {"desktop": desktop})
        else:
            self.original_settings = None

    def _set_linux_proxy(self, host, port, socks_port=10808):
        import os
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
        if "GNOME" in desktop or "Ubuntu" in desktop:
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "manual"])
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "host", host])
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "port", str(port)])
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "host", host])
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "port", str(port)])
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "host", host])
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "port", str(socks_port)])
        elif "KDE" in desktop:
            subprocess.run(["kwriteconfig6", "--file", "kioslaverc", "--group", "Proxy Settings", "--key", "ProxyType", "1"])
            subprocess.run(["kwriteconfig6", "--file", "kioslaverc", "--group", "Proxy Settings", "--key", "httpProxy", f"http://{host}:{port}"])
            subprocess.run(["kwriteconfig6", "--file", "kioslaverc", "--group", "Proxy Settings", "--key", "httpsProxy", f"http://{host}:{port}"])
            subprocess.run(["kwriteconfig6", "--file", "kioslaverc", "--group", "Proxy Settings", "--key", "socksProxy", f"{host}:{socks_port}"])
            subprocess.run(["dbus-send", "--type=signal", "/KIO/Scheduler", "org.kde.KIO.Scheduler.reparseSlaveConfiguration", "string:''"])
        elif "XFCE" in desktop.upper():
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/http/enabled", "-s", "true"])
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/http/host", "-s", host])
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/http/port", "-s", str(port)])
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/https/enabled", "-s", "true"])
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/https/host", "-s", host])
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/https/port", "-s", str(port)])
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/socks/enabled", "-s", "true"])
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/socks/host", "-s", host])
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/socks/port", "-s", str(socks_port)])
        else:
            # 不支持的桌面环境：尝试 gsettings（某些非 GNOME 桌面也使用 dconf/gsettings）
            # gsettings 是 Linux 桌面代理设置的事实标准，许多应用（如 Firefox、Chrome）会读取
            gsettings_ok = False
            try:
                result = subprocess.run(
                    ["gsettings", "set", "org.gnome.system.proxy", "mode", "manual"],
                    capture_output=True, timeout=3
                )
                if result.returncode == 0:
                    subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "host", host], capture_output=True, timeout=3)
                    subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "port", str(port)], capture_output=True, timeout=3)
                    subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "host", host], capture_output=True, timeout=3)
                    subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "port", str(port)], capture_output=True, timeout=3)
                    subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "host", host], capture_output=True, timeout=3)
                    subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "port", str(socks_port)], capture_output=True, timeout=3)
                    gsettings_ok = True
                    logger.info(f"Set system proxy via gsettings (desktop={desktop})")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            # 设置环境变量作为 fallback（当前进程及其子进程生效）
            os.environ["http_proxy"] = f"http://{host}:{port}"
            os.environ["https_proxy"] = f"http://{host}:{port}"
            os.environ["HTTP_PROXY"] = f"http://{host}:{port}"
            os.environ["HTTPS_PROXY"] = f"http://{host}:{port}"
            os.environ["all_proxy"] = f"socks5://{host}:{socks_port}"
            os.environ["ALL_PROXY"] = f"socks5://{host}:{socks_port}"
            os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
            os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
            # 写入代理环境变量到文件，用户可在终端中 source 此文件
            try:
                # Backward compat: use legacy ~/.venlta if it exists, otherwise platform data dir
                _legacy = Path.home() / ".venlta"
                proxy_env_path = (_legacy if _legacy.exists() else Path(get_data_dir())) / "proxy.env"
                with open(proxy_env_path, 'w', encoding='utf-8') as f:
                    f.write(f"export http_proxy=http://{host}:{port}\n")
                    f.write(f"export https_proxy=http://{host}:{port}\n")
                    f.write(f"export HTTP_PROXY=http://{host}:{port}\n")
                    f.write(f"export HTTPS_PROXY=http://{host}:{port}\n")
                    f.write(f"export all_proxy=socks5://{host}:{socks_port}\n")
                    f.write(f"export ALL_PROXY=socks5://{host}:{socks_port}\n")
                    f.write(f"export no_proxy=localhost,127.0.0.1,::1\n")
                    f.write(f"export NO_PROXY=localhost,127.0.0.1,::1\n")
                if not gsettings_ok:
                    logger.warning(f"Unsupported Linux desktop: {desktop}. Environment variables set. "
                                   f"Source ~/.venlta/proxy.env in your terminal for CLI apps.")
            except Exception as e:
                logger.debug(f"Failed to write proxy.env: {e}")

    # ---- macOS ----
    def _backup_macos(self):
        """Backup macOS proxy settings for all network services

        macOS uses networksetup to configure proxy per network service.
        Each service (Wi-Fi, Ethernet, etc.) has its own proxy settings.
        """
        services = _get_macos_network_services()
        if not services:
            logger.warning("No macOS network services found")
            self.original_settings = ("macOS", {"services": []})
            return

        backup = {"services": services}
        for service in services:
            service_backup = {}
            try:
                # HTTP proxy
                result = subprocess.run(
                    ["networksetup", "-getwebproxy", service],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    service_backup["http_enabled"] = lines[0].split(':')[-1].strip() if len(lines) > 0 else "No"
                    service_backup["http_host"] = lines[1].split(':')[-1].strip() if len(lines) > 1 else ""
                    service_backup["http_port"] = lines[2].split(':')[-1].strip() if len(lines) > 2 else ""

                # HTTPS proxy
                result = subprocess.run(
                    ["networksetup", "-getsecurewebproxy", service],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    service_backup["https_enabled"] = lines[0].split(':')[-1].strip() if len(lines) > 0 else "No"
                    service_backup["https_host"] = lines[1].split(':')[-1].strip() if len(lines) > 1 else ""
                    service_backup["https_port"] = lines[2].split(':')[-1].strip() if len(lines) > 2 else ""

                # SOCKS proxy
                result = subprocess.run(
                    ["networksetup", "-getsocksfirewallproxy", service],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    service_backup["socks_enabled"] = lines[0].split(':')[-1].strip() if len(lines) > 0 else "No"
                    service_backup["socks_host"] = lines[1].split(':')[-1].strip() if len(lines) > 1 else ""
                    service_backup["socks_port"] = lines[2].split(':')[-1].strip() if len(lines) > 2 else ""

                backup[service] = service_backup
            except (subprocess.TimeoutExpired, Exception) as e:
                logger.warning(f"Failed to backup proxy for service '{service}': {e}")

        self.original_settings = ("macOS", backup)

    def _set_macos_proxy(self, host, port, socks_port=10808):
        """Set macOS system proxy for all network services

        Uses networksetup command-line tool to configure HTTP, HTTPS,
        and SOCKS proxy for each network service.
        """
        services = _get_macos_network_services()
        if not services:
            logger.warning("No macOS network services found, cannot set proxy")
            return

        for service in services:
            try:
                # HTTP proxy
                subprocess.run(
                    ["networksetup", "-setwebproxy", service, host, str(port)],
                    capture_output=True, timeout=5
                )
                subprocess.run(
                    ["networksetup", "-setwebproxystate", service, "on"],
                    capture_output=True, timeout=5
                )

                # HTTPS proxy
                subprocess.run(
                    ["networksetup", "-setsecurewebproxy", service, host, str(port)],
                    capture_output=True, timeout=5
                )
                subprocess.run(
                    ["networksetup", "-setsecurewebproxystate", service, "on"],
                    capture_output=True, timeout=5
                )

                # SOCKS proxy
                subprocess.run(
                    ["networksetup", "-setsocksfirewallproxy", service, host, str(socks_port)],
                    capture_output=True, timeout=5
                )
                subprocess.run(
                    ["networksetup", "-setsocksfirewallproxystate", service, "on"],
                    capture_output=True, timeout=5
                )
            except (subprocess.TimeoutExpired, Exception) as e:
                logger.warning(f"Failed to set proxy for service '{service}': {e}")

    # ---- Restore ----
    def _restore(self):
        if not self.original_settings:
            return
        try:
            platform_type = self.original_settings[0]
            if platform_type == "Windows":
                self._restore_windows()
            elif platform_type.startswith("Linux"):
                self._restore_linux()
            elif platform_type == "macOS":
                self._restore_macos()
        except Exception as e:
            logger.error(f"Failed to restore proxy settings: {e}")

    def _restore_windows(self):
        import winreg
        import ctypes
        _, saved = self.original_settings
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, saved["enabled"])
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, saved["server"])
        if "override" in saved:
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, saved["override"])
        if "auto_config_url" in saved:
            winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, saved["auto_config_url"])
        winreg.CloseKey(key)
        ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        ctypes.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)

    def _restore_linux(self):
        import os
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
        platform_type = self.original_settings[0]
        if "GNOME" in desktop and platform_type.startswith("Linux-Xfce"):
            logger.warning(f"Desktop environment changed: backup was {platform_type}, current is GNOME")
        elif "KDE" in desktop and platform_type.startswith("Linux-GNOME"):
            logger.warning(f"Desktop environment changed: backup was {platform_type}, current is KDE")
        if platform_type == "Linux-GNOME" and ("GNOME" in desktop or "Ubuntu" in desktop):
            saved = self.original_settings[1] if len(self.original_settings) > 1 else {"mode": "'none'"}
            def _strip_gvariant(v: str) -> str:
                if isinstance(v, str) and len(v) >= 2 and v[0] == "'" and v[-1] == "'":
                    return v[1:-1]
                return v
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "mode", _strip_gvariant(saved.get("mode", "'none'"))])
            if saved.get("http_host"):
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "host", _strip_gvariant(saved["http_host"])])
            if saved.get("http_port"):
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "port", saved["http_port"]])
            if saved.get("https_host"):
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "host", _strip_gvariant(saved["https_host"])])
            if saved.get("https_port"):
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "port", saved["https_port"]])
            if saved.get("socks_host"):
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "host", _strip_gvariant(saved["socks_host"])])
            if saved.get("socks_port"):
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "port", saved["socks_port"]])
        elif platform_type == "Linux-KDE" and "KDE" in desktop:
            saved = self.original_settings[1] if len(self.original_settings) > 1 else {}
            proxy_type = saved.get("proxy_type", "0")
            subprocess.run(["kwriteconfig6", "--file", "kioslaverc", "--group", "Proxy Settings", "--key", "ProxyType", proxy_type])
            if saved.get("http_proxy"):
                subprocess.run(["kwriteconfig6", "--file", "kioslaverc", "--group", "Proxy Settings", "--key", "httpProxy", saved["http_proxy"]])
            if saved.get("https_proxy"):
                subprocess.run(["kwriteconfig6", "--file", "kioslaverc", "--group", "Proxy Settings", "--key", "httpsProxy", saved["https_proxy"]])
            if saved.get("socks_proxy"):
                subprocess.run(["kwriteconfig6", "--file", "kioslaverc", "--group", "Proxy Settings", "--key", "socksProxy", saved["socks_proxy"]])
            subprocess.run(["dbus-send", "--type=signal", "/KIO/Scheduler", "org.kde.KIO.Scheduler.reparseSlaveConfiguration", "string:''"])
        elif platform_type == "Linux-Xfce" and "XFCE" in desktop.upper():
            saved = self.original_settings[1] if len(self.original_settings) > 1 else {}
            http_enabled = saved.get("http_enabled", "false")
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/http/enabled", "-s", http_enabled])
            if saved.get("http_host"):
                subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/http/host", "-s", saved["http_host"]])
            if saved.get("http_port"):
                subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/http/port", "-s", saved["http_port"]])
            https_enabled = saved.get("https_enabled", "false")
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/https/enabled", "-s", https_enabled])
            if saved.get("https_host"):
                subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/https/host", "-s", saved["https_host"]])
            if saved.get("https_port"):
                subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/https/port", "-s", saved["https_port"]])
            socks_enabled = saved.get("socks_enabled", "false")
            subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/socks/enabled", "-s", socks_enabled])
            if saved.get("socks_host"):
                subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/socks/host", "-s", saved["socks_host"]])
            if saved.get("socks_port"):
                subprocess.run(["xfconf-query", "-c", "xfce4-session", "-p", "/proxy/socks/port", "-s", saved["socks_port"]])
        # 清理环境变量和 proxy.env 文件
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                     "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY"):
            os.environ.pop(key, None)
        try:
            # Backward compat: use legacy ~/.venlta if it exists, otherwise platform data dir
            _legacy = Path.home() / ".venlta"
            proxy_env_path = (_legacy if _legacy.exists() else Path(get_data_dir())) / "proxy.env"
            if proxy_env_path.exists():
                proxy_env_path.unlink()
        except Exception:
            pass

    def _restore_macos(self):
        """Restore macOS proxy settings for all network services"""
        _, saved = self.original_settings
        services = saved.get("services", [])

        for service in services:
            service_backup = saved.get(service, {})
            if not service_backup:
                # No backup for this service, disable all proxies
                try:
                    subprocess.run(["networksetup", "-setwebproxystate", service, "off"], capture_output=True, timeout=5)
                    subprocess.run(["networksetup", "-setsecurewebproxystate", service, "off"], capture_output=True, timeout=5)
                    subprocess.run(["networksetup", "-setsocksfirewallproxystate", service, "off"], capture_output=True, timeout=5)
                except Exception:
                    pass
                continue

            try:
                # Restore HTTP proxy
                http_enabled = service_backup.get("http_enabled", "No")
                if http_enabled == "Yes":
                    http_host = service_backup.get("http_host", "")
                    http_port = service_backup.get("http_port", "")
                    if http_host and http_port:
                        subprocess.run(
                            ["networksetup", "-setwebproxy", service, http_host, http_port],
                            capture_output=True, timeout=5
                        )
                    subprocess.run(["networksetup", "-setwebproxystate", service, "on"], capture_output=True, timeout=5)
                else:
                    subprocess.run(["networksetup", "-setwebproxystate", service, "off"], capture_output=True, timeout=5)

                # Restore HTTPS proxy
                https_enabled = service_backup.get("https_enabled", "No")
                if https_enabled == "Yes":
                    https_host = service_backup.get("https_host", "")
                    https_port = service_backup.get("https_port", "")
                    if https_host and https_port:
                        subprocess.run(
                            ["networksetup", "-setsecurewebproxy", service, https_host, https_port],
                            capture_output=True, timeout=5
                        )
                    subprocess.run(["networksetup", "-setsecurewebproxystate", service, "on"], capture_output=True, timeout=5)
                else:
                    subprocess.run(["networksetup", "-setsecurewebproxystate", service, "off"], capture_output=True, timeout=5)

                # Restore SOCKS proxy
                socks_enabled = service_backup.get("socks_enabled", "No")
                if socks_enabled == "Yes":
                    socks_host = service_backup.get("socks_host", "")
                    socks_port = service_backup.get("socks_port", "")
                    if socks_host and socks_port:
                        subprocess.run(
                            ["networksetup", "-setsocksfirewallproxy", service, socks_host, socks_port],
                            capture_output=True, timeout=5
                        )
                    subprocess.run(["networksetup", "-setsocksfirewallproxystate", service, "on"], capture_output=True, timeout=5)
                else:
                    subprocess.run(["networksetup", "-setsocksfirewallproxystate", service, "off"], capture_output=True, timeout=5)

            except (subprocess.TimeoutExpired, Exception) as e:
                logger.warning(f"Failed to restore proxy for service '{service}': {e}")
