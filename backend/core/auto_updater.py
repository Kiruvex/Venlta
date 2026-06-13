"""自动更新管理器

应用与 sing-box 核心的更新检查、下载、校验。

防 403 限速策略：
- ETag 条件请求：发送 If-None-Match 头，304 响应不计入限速配额
- 持久化 ETag 缓存：ETag 和上次结果写入磁盘，重启后仍有效
- 长缓存时间：内存缓存 1 小时（原 5 分钟），避免频繁请求
- 403/429 识别：GitHub 未认证限速返回 403 + X-RateLimit-Remaining=0

安全设计：
- 下载包使用 SHA256 校验，防止篡改
- 不自动安装，下载后提示用户确认
- sing-box 核心更新独立于应用更新
"""

import httpx
import json
import os
import platform
import subprocess
import tempfile
import hashlib
import logging
import re
import time as _time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# GitHub 仓库（示例仓库，实际替换为真实仓库）
GITHUB_REPO = "venlta/venlta"
SINGBOX_REPO = "SagerNet/sing-box"

# 内存缓存有效期（秒），304 响应也刷新此计时器
_CACHE_TTL = 3600  # 1 小时

# ETag 持久化缓存路径
def _get_etag_cache_path() -> Path:
    from utils.constants import get_data_dir
    return Path(get_data_dir()) / "update_etag_cache.json"


class AutoUpdater:
    """应用与 sing-box 核心更新器

    返回值约定：
    - dict（含 version/download_url 等）：有新版本
    - dict（含 ok=False, error）：检查失败
    - None：无新版本
    """

    def __init__(self):
        self.current_version = self._get_current_version()
        # 内存缓存：{repo: (timestamp, result)}，避免短时间内重复请求
        self._last_check: dict[str, tuple[float, Optional[dict]]] = {}
        # ETag 持久化缓存：{repo: {"etag": "...", "result": ...}}
        self._etag_cache: dict[str, dict] = self._load_etag_cache()

    def _compare_versions(self, v1: str, v2: str) -> int:
        """比较语义版本号，支持预发布标识

        Returns:
            1 if v1 > v2, -1 if v1 < v2, 0 if equal

        规则：
        - 数字部分按数值比较（1.10 > 1.9）
        - 预发布标识（alpha/beta/rc）低于同版本号正式版
        - 预发布标识之间按字典序比较
        """
        def parse_ver(v: str) -> tuple[list[int], str]:
            v = v.lstrip('v')
            match = re.match(r'^(\d+(?:\.\d+)*)', v)
            if not match:
                return [0], v
            version_part = match.group(1)
            prerelease = v[len(version_part):]
            parts = [int(p) for p in version_part.split('.') if p.isdigit()]
            return parts, prerelease

        p1, pre1 = parse_ver(v1)
        p2, pre2 = parse_ver(v2)

        # 数字部分逐位比较
        for a, b in zip(p1, p2):
            if a > b:
                return 1
            if a < b:
                return -1
        # 长版本号 > 短版本号（1.0.0 > 1.0）
        if len(p1) > len(p2):
            return 1
        if len(p1) < len(p2):
            return -1

        # 同版本号，有预发布标识的更低
        if pre1 and not pre2:
            return -1
        if not pre1 and pre2:
            return 1
        if pre1 and pre2:
            return -1 if pre1 < pre2 else (1 if pre1 > pre2 else 0)
        return 0

    def _get_current_version(self) -> str:
        """读取当前版本号

        路径推导：此文件位于 backend/core/auto_updater.py，
        parent 为 backend/core/，parent.parent 为 backend/，
        parent.parent.parent 为项目根目录，
        VERSION 文件位于项目根目录（与 backend/ 同级）。

        注意：如果使用 Nuitka 打包，Path(__file__) 可能指向临时解压目录，
        此时应在 main.py 中通过环境变量 VENLTA_VERSION 传入版本号。
        """
        # 优先使用环境变量（Nuitka 打包场景）
        env_version = os.environ.get('VENLTA_VERSION')
        if env_version:
            return env_version.strip()
        version_file = Path(__file__).parent.parent.parent / "VERSION"
        if version_file.exists():
            return version_file.read_text().strip()
        return "0.0.0"

    def _load_etag_cache(self) -> dict:
        """从磁盘加载 ETag 缓存（跨重启持久化）"""
        try:
            path = _get_etag_cache_path()
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug(f"Failed to load ETag cache: {e}")
        return {}

    def _save_etag_cache(self):
        """将 ETag 缓存写入磁盘"""
        try:
            path = _get_etag_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._etag_cache, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Failed to save ETag cache: {e}")

    def _github_get(self, url: str, extra_headers: dict | None = None) -> httpx.Response:
        """发送 GitHub API 请求，自动附加 GITHUB_TOKEN 和自定义头"""
        headers = {}
        github_token = os.environ.get('GITHUB_TOKEN')
        if github_token:
            headers['Authorization'] = f'token {github_token}'
        if extra_headers:
            headers.update(extra_headers)
        return httpx.get(url, timeout=10, headers=headers)

    def _check_rate_limit(self, resp: httpx.Response, cache_key: str) -> Optional[dict]:
        """检查 GitHub API 限速响应，若限速则返回错误 dict，否则返回 None"""
        is_rate_limited = (
            resp.status_code == 429 or
            (resp.status_code == 403 and resp.headers.get('X-RateLimit-Remaining') == '0')
        )
        if is_rate_limited:
            reset_time = resp.headers.get('X-RateLimit-Reset', '')
            logger.warning(f"GitHub API rate limit hit (HTTP {resp.status_code}). Reset at: {reset_time}")
            result = {"ok": False, "error": "GitHub API rate limit exceeded. Please try again later."}
            self._last_check[cache_key] = (_time.time(), result)
            return result
        # 检查剩余请求配额，提前预警
        remaining = resp.headers.get('X-RateLimit-Remaining')
        if remaining is not None and int(remaining) <= 5:
            logger.warning(f"GitHub API rate limit nearly exhausted: {remaining} requests remaining")
        return None

    def check_update(self) -> Optional[dict]:
        """检查 GitHub Release 是否有新版本

        返回值：
        - dict: 有新版本时返回版本信息
        - dict（含 ok=False, error）: 检查失败时返回错误信息
        - None: 无新版本

        防 403 机制：
        1. 内存缓存 1 小时内不重复请求
        2. 发送 If-None-Match 头，304 不计入限速配额
        3. 304 时直接返回上次缓存的解析结果
        """
        cache_key = GITHUB_REPO
        # 内存缓存：1 小时内不重复请求
        if cache_key in self._last_check:
            cached_ts, cached_result = self._last_check[cache_key]
            if _time.time() - cached_ts < _CACHE_TTL:
                return cached_result
        try:
            system = platform.system().lower()
            machine = "amd64" if platform.machine() in ("x86_64", "AMD64") else "arm64"

            # ETag 条件请求：发送 If-None-Match，304 不计入限速配额
            etag_headers = {}
            cached_etag = self._etag_cache.get(cache_key, {}).get("etag")
            if cached_etag:
                etag_headers["If-None-Match"] = cached_etag

            resp = self._github_get(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                etag_headers,
            )

            # 限速检查
            rate_limit_result = self._check_rate_limit(resp, cache_key)
            if rate_limit_result is not None:
                return rate_limit_result

            # 304 Not Modified：Release 没有变化，复用上次解析结果
            if resp.status_code == 304:
                logger.debug(f"GitHub API 304 for {cache_key}, reusing cached result")
                cached_entry = self._etag_cache.get(cache_key, {})
                cached_result = cached_entry.get("result")
                # 304 也刷新内存缓存计时器
                self._last_check[cache_key] = (_time.time(), cached_result)
                return cached_result

            if resp.status_code != 200:
                result = {"ok": False, "error": f"GitHub API returned {resp.status_code}"}
                self._last_check[cache_key] = (_time.time(), result)
                return result

            # 200：保存新 ETag
            new_etag = resp.headers.get("ETag")
            release = resp.json()
            latest = release["tag_name"].lstrip("v")
            if self._compare_versions(latest, self.current_version) <= 0:
                self._last_check[cache_key] = (_time.time(), None)
                # 持久化 ETag + None 结果（下次 304 直接返回 None）
                if new_etag:
                    self._etag_cache[cache_key] = {"etag": new_etag, "result": None}
                    self._save_etag_cache()
                return None  # 无新版本

            # 查找匹配平台的资源
            asset_name = f"venlta-{system}-{machine}"
            for asset in release.get("assets", []):
                if asset_name in asset["name"]:
                    result = {
                        "version": latest,
                        "download_url": asset["browser_download_url"],
                        "sha256_url": asset["browser_download_url"] + ".sha256",
                        "release_notes": release.get("body", ""),
                        "size": asset.get("size", 0),
                    }
                    self._last_check[cache_key] = (_time.time(), result)
                    # 持久化 ETag + 结果
                    if new_etag:
                        self._etag_cache[cache_key] = {"etag": new_etag, "result": result}
                        self._save_etag_cache()
                    return result
            # 未找到匹配当前平台的资源
            err_result = {"ok": False, "error": f"No matching asset for {asset_name}", "has_new_version": True}
            self._last_check[cache_key] = (_time.time(), err_result)
            return err_result
        except Exception as e:
            logger.error(f"Failed to check update: {e}")
            return {"ok": False, "error": str(e)}

    def check_singbox_update(self) -> Optional[dict]:
        """检查 sing-box 核心是否有新版本

        与 check_update 共享 ETag 条件请求和缓存机制。
        """
        cache_key = SINGBOX_REPO
        # 内存缓存
        if cache_key in self._last_check:
            cached_ts, cached_result = self._last_check[cache_key]
            if _time.time() - cached_ts < _CACHE_TTL:
                return cached_result
        try:
            # ETag 条件请求
            etag_headers = {}
            cached_etag = self._etag_cache.get(cache_key, {}).get("etag")
            if cached_etag:
                etag_headers["If-None-Match"] = cached_etag

            resp = self._github_get(
                f"https://api.github.com/repos/{SINGBOX_REPO}/releases/latest",
                etag_headers,
            )

            # 限速检查
            rate_limit_result = self._check_rate_limit(resp, cache_key)
            if rate_limit_result is not None:
                return rate_limit_result

            # 304 Not Modified：复用上次解析结果
            if resp.status_code == 304:
                logger.debug(f"GitHub API 304 for {cache_key}, reusing cached result")
                cached_entry = self._etag_cache.get(cache_key, {})
                cached_result = cached_entry.get("result")
                self._last_check[cache_key] = (_time.time(), cached_result)
                return cached_result

            if resp.status_code != 200:
                err_result = {"ok": False, "error": f"GitHub API returned {resp.status_code}"}
                self._last_check[cache_key] = (_time.time(), err_result)
                return err_result

            new_etag = resp.headers.get("ETag")
            release = resp.json()
            latest = release["tag_name"].lstrip("v")
            # 获取当前 sing-box 版本
            current = self._get_current_singbox_version()
            if self._compare_versions(latest, current) <= 0:
                self._last_check[cache_key] = (_time.time(), None)
                if new_etag:
                    self._etag_cache[cache_key] = {"etag": new_etag, "result": None}
                    self._save_etag_cache()
                return None
            system = platform.system().lower()
            machine = "amd64" if platform.machine() in ("x86_64", "AMD64") else "arm64"
            asset_name = f"sing-box-{latest}-{system}-{machine}"
            for asset in release.get("assets", []):
                if asset_name in asset["name"] and not asset["name"].endswith('.sha256'):
                    result = {
                        "version": latest,
                        "download_url": asset["browser_download_url"],
                    }
                    # Find corresponding .sha256 file
                    sha256_name = asset["name"] + ".sha256"
                    for sha_asset in release.get("assets", []):
                        if sha_asset["name"] == sha256_name:
                            result["sha256_url"] = sha_asset["browser_download_url"]
                            break
                    self._last_check[cache_key] = (_time.time(), result)
                    if new_etag:
                        self._etag_cache[cache_key] = {"etag": new_etag, "result": result}
                        self._save_etag_cache()
                    return result
            err_result = {"ok": False, "error": f"No matching platform asset found for sing-box {latest}", "has_new_version": True}
            self._last_check[cache_key] = (_time.time(), err_result)
            return err_result
        except Exception as e:
            logger.error(f"Failed to check sing-box update: {e}")
            return {"ok": False, "error": str(e)}

    def _get_current_singbox_version(self) -> str:
        """获取当前安装的 sing-box 版本号"""
        try:
            from core.config_manager import find_singbox_binary
            singbox_bin = find_singbox_binary()
            if not singbox_bin:
                return "0.0.0"
            result = subprocess.run(
                [singbox_bin, "version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                version_match = re.search(r'(\d+\.\d+\.\d+)', result.stdout.strip())
                return version_match.group(1) if version_match else "0.0.0"
            return "0.0.0"
        except FileNotFoundError:
            logger.warning("sing-box binary not found, cannot check for core updates.")
            return "0.0.0"
        except Exception as e:
            logger.warning(f"Failed to get sing-box version: {e}")
            return "0.0.0"

    def check_updates_on_startup(self, callback=None):
        """Check both app and core updates on startup

        Calls the callback with a dict containing 'app' and/or 'core' keys
        if updates are available. Respects the auto_update_enabled setting
        (should be checked by the caller before invoking this method).

        Args:
            callback: Optional callable receiving the result dict
        """
        result = {}
        try:
            app_update = self.check_update()
            if app_update and isinstance(app_update, dict) and app_update.get("ok", True) and app_update.get("version"):
                result["app"] = app_update
        except Exception as e:
            logger.debug(f"Startup app update check failed: {e}")
        try:
            core_update = self.check_singbox_update()
            if core_update and isinstance(core_update, dict) and core_update.get("ok", True) and core_update.get("version"):
                result["core"] = core_update
        except Exception as e:
            logger.debug(f"Startup core update check failed: {e}")
        if callback:
            callback(result)
        return result

    def install_core_update(self, archive_path: str) -> dict:
        """Install downloaded sing-box core update

        Extracts the sing-box binary from a .tar.gz or .zip archive and replaces
        the current binary. The new binary is verified by running 'sing-box version'
        before replacing.

        Args:
            archive_path: Path to the downloaded archive file (.tar.gz or .zip)

        Returns:
            {"ok": True} on success, {"ok": False, "error": "..."} on failure
        """
        import tarfile
        import zipfile
        import shutil
        import stat

        # Platform-specific binary name
        binary_name = "sing-box.exe" if platform.system() == "Windows" else "sing-box"

        try:
            archive = Path(archive_path)
            if not archive.exists():
                return {"ok": False, "error": f"Archive not found: {archive_path}"}

            # Find current sing-box binary location
            from core.config_manager import find_singbox_binary
            current_binary = find_singbox_binary()
            if current_binary:
                current_binary = Path(current_binary)
                install_dir = current_binary.parent
            else:
                # Fallback: use the backend/sing-box directory
                install_dir = Path(__file__).parent.parent / "sing-box"
                install_dir.mkdir(parents=True, exist_ok=True)

            # Extract to a temporary location first
            extract_dir = Path(archive_path).parent / "singbox_update_tmp"
            extract_dir.mkdir(parents=True, exist_ok=True)

            # Detect archive format by extension and extract sing-box binary
            archive_suffix = archive.name.lower()
            if archive_suffix.endswith('.zip'):
                # Windows releases come as .zip
                with zipfile.ZipFile(archive_path, 'r') as zf:
                    # Find the sing-box binary in the archive
                    singbox_entry = None
                    for entry in zf.namelist():
                        basename = Path(entry).name
                        if basename == binary_name and not entry.endswith('/'):
                            singbox_entry = entry
                            break

                    if not singbox_entry:
                        # Try case-insensitive search
                        for entry in zf.namelist():
                            basename = Path(entry).name.lower()
                            if basename == binary_name.lower() and not entry.endswith('/'):
                                singbox_entry = entry
                                break

                    if not singbox_entry:
                        return {"ok": False, "error": f"{binary_name} not found in archive"}

                    # Extract just the binary file
                    zf.extract(singbox_entry, extract_dir)
                    # Move to expected location with correct name
                    extracted_src = extract_dir / singbox_entry
                    extracted_dst = extract_dir / binary_name
                    if extracted_src != extracted_dst:
                        extracted_src.rename(extracted_dst)
            elif archive_suffix.endswith('.tar.gz') or archive_suffix.endswith('.tgz'):
                # Linux/macOS releases come as .tar.gz
                with tarfile.open(archive_path, 'r:gz') as tar:
                    # Find the sing-box binary in the archive
                    singbox_members = []
                    for member in tar.getmembers():
                        # Look for sing-box binary (might be in a subdirectory like sing-box-1.13.0-linux-amd64/)
                        if member.name.endswith(f'/{binary_name}') or member.name == binary_name:
                            singbox_members.append(member)

                    if not singbox_members:
                        # Try case-insensitive search
                        for member in tar.getmembers():
                            basename = Path(member.name).name.lower()
                            if basename == binary_name.lower() and member.isfile():
                                singbox_members.append(member)

                    if not singbox_members:
                        return {"ok": False, "error": f"{binary_name} not found in archive"}

                    # Extract the first matching binary
                    target_member = singbox_members[0]
                    target_member.name = binary_name  # Rename to expected binary name
                    try:
                        tar.extract(target_member, extract_dir, filter='data')
                    except TypeError:
                        # Python < 3.12 does not support filter parameter
                        tar.extract(target_member, extract_dir)
            else:
                return {"ok": False, "error": f"Unsupported archive format: {archive.name}"}

            extracted_binary = extract_dir / binary_name
            if not extracted_binary.exists():
                return {"ok": False, "error": f"Failed to extract {binary_name}"}

            # Make executable (Unix only — chmod is meaningless on Windows)
            if platform.system() != "Windows":
                extracted_binary.chmod(extracted_binary.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

            # Verify the new binary works
            try:
                test_result = subprocess.run(
                    [str(extracted_binary), "version"],
                    capture_output=True, text=True, timeout=5
                )
                if test_result.returncode != 0:
                    logger.error(f"New sing-box binary verification failed: {test_result.stderr}")
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    return {"ok": False, "error": "New binary verification failed"}
            except Exception as verify_err:
                logger.error(f"Cannot verify new sing-box binary: {verify_err}")
                shutil.rmtree(extract_dir, ignore_errors=True)
                return {"ok": False, "error": f"Binary verification error: {verify_err}"}

            # Stop sing-box before replacing (caller should handle this)
            # Replace the binary
            dest_binary = install_dir / binary_name
            if current_binary and current_binary.exists():
                # Backup old binary
                backup_path = current_binary.with_suffix('.bak')
                try:
                    shutil.copy2(current_binary, backup_path)
                except Exception:
                    pass  # Non-critical

                # Replace binary
                try:
                    shutil.copy2(extracted_binary, current_binary)
                    logger.info(f"Replaced sing-box binary: {current_binary}")
                except PermissionError:
                    # Try renaming (works if on same filesystem)
                    try:
                        current_binary.unlink()
                        shutil.copy2(extracted_binary, current_binary)
                    except Exception as replace_err:
                        # Restore backup
                        if backup_path.exists():
                            shutil.copy2(backup_path, current_binary)
                        return {"ok": False, "error": f"Permission denied replacing binary: {replace_err}"}
            else:
                # No existing binary, just copy to destination
                shutil.copy2(extracted_binary, dest_binary)
                logger.info(f"Installed sing-box binary: {dest_binary}")

            # Cleanup
            shutil.rmtree(extract_dir, ignore_errors=True)
            try:
                archive.unlink()
            except Exception:
                pass

            # Clear version cache since we've updated
            if SINGBOX_REPO in self._last_check:
                del self._last_check[SINGBOX_REPO]

            return {"ok": True}

        except Exception as e:
            logger.error(f"Failed to install core update: {e}")
            return {"ok": False, "error": str(e)}

    def install_app_update(self, archive_path: str) -> dict:
        """Install downloaded app update

        Currently a placeholder - app auto-install requires platform-specific
        installers and restart logic that isn't implemented yet.

        Args:
            archive_path: Path to the downloaded update archive

        Returns:
            {"ok": False, "error": "..."} - not yet implemented
        """
        return {"ok": False, "error": "App auto-install is not yet supported. Please install the update manually."}

    def download_and_verify(self, url: str, sha256_url: str) -> Optional[Path]:
        """下载更新包并验证 SHA256

        Args:
            url: 下载链接
            sha256_url: SHA256 校验文件链接（<asset_url>.sha256）

        Returns:
            下载文件路径；校验失败返回 None

        安全说明：
        - 下载的文件先保存到临时目录
        - 下载完成后与 .sha256 文件中的校验和比对
        - 校验失败则删除临时文件，返回 None
        """
        try:
            # 流式下载，支持大文件
            # Derive suffix from URL so install_core_update can detect format
            url_lower = url.lower()
            if url_lower.endswith('.tar.gz'):
                suffix = '.tar.gz'
            elif url_lower.endswith('.tgz'):
                suffix = '.tgz'
            else:
                suffix = '.zip'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                with httpx.stream("GET", url, timeout=120) as resp:
                    for chunk in resp.iter_bytes(chunk_size=8192):
                        tmp.write(chunk)
                tmp_path = Path(tmp.name)

            # 验证 SHA256
            sha256_resp = httpx.get(sha256_url, timeout=10)
            if sha256_resp.status_code != 200:
                logger.error(f"Failed to download SHA256 file: HTTP {sha256_resp.status_code}")
                tmp_path.unlink()
                return None
            expected_hash = sha256_resp.text.strip().split()[0]
            actual_hash = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                logger.error(f"SHA256 mismatch: expected {expected_hash}, got {actual_hash}")
                tmp_path.unlink()
                return None
            logger.info(f"Download verified: {tmp_path} (SHA256 match)")
            return tmp_path
        except Exception as e:
            logger.error(f"Download/verify failed: {e}")
            return None
