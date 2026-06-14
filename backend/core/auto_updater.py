"""自动更新管理器

应用更新检查、下载、校验。sing-box 核心下载（固定版本 1.13.13）。

防 403 限速策略：
- ETag 条件请求：发送 If-None-Match 头，304 响应不计入限速配额
- 持久化 ETag 缓存：ETag 和上次结果写入磁盘，重启后仍有效
- 长缓存时间：内存缓存 1 小时，避免频繁请求
- 403/429 识别：GitHub 未认证限速返回 403 + X-RateLimit-Remaining=0

安全设计：
- 下载包使用 SHA256 校验，防止篡改
- 不自动安装，下载后提示用户确认
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

# GitHub 仓库
GITHUB_REPO = "Kiruvex/Venlta"
SINGBOX_REPO = "SagerNet/sing-box"

# sing-box 固定下载版本
SINGBOX_VERSION = "1.13.13"

# sing-box 归档文件 SHA256（硬编码校验，防止篡改）
SINGBOX_SHA256 = {
    "linux-amd64": "bb99cabf47694625db421ee17898f36cdc1f9c2cb5decf65b12bac8d8437e842",
    "windows-amd64": "aea1fa983134a2e2d0600581d1178e98bd6bb93ae12ad8c333eaacae68a1694c",
}

# 内存缓存有效期（秒），304 响应也刷新此计时器
_CACHE_TTL = 14400  # 4 小时（避免频繁请求导致 rate limit）

# ETag 持久化缓存路径
def _get_etag_cache_path() -> Path:
    from utils.constants import get_data_dir
    return Path(get_data_dir()) / "update_etag_cache.json"


class AutoUpdater:
    """应用更新器 + sing-box 核心下载器

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

        for a, b in zip(p1, p2):
            if a > b: return 1
            if a < b: return -1
        if len(p1) > len(p2): return 1
        if len(p1) < len(p2): return -1

        if pre1 and not pre2: return -1
        if not pre1 and pre2: return 1
        if pre1 and pre2:
            return -1 if pre1 < pre2 else (1 if pre1 > pre2 else 0)
        return 0

    def _get_current_version(self) -> str:
        """读取当前版本号"""
        env_version = os.environ.get('VENLTA_VERSION')
        if env_version:
            return env_version.strip()
        version_file = Path(__file__).parent.parent.parent / "VERSION"
        if version_file.exists():
            return version_file.read_text(encoding='utf-8').strip()
        return "0.0.0"

    def _load_etag_cache(self) -> dict:
        """从磁盘加载 ETag 缓存"""
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
        """发送 GitHub API 请求，自动附加 GITHUB_TOKEN"""
        headers = {}
        github_token = os.environ.get('GITHUB_TOKEN')
        if github_token:
            headers['Authorization'] = f'token {github_token}'
        if extra_headers:
            headers.update(extra_headers)
        return httpx.get(url, timeout=10, headers=headers, follow_redirects=True)

    # 限速时的哨兵返回值（与 None 和普通 dict 区分）
    _RATE_LIMITED = object()

    def _check_rate_limit(self, resp: httpx.Response, cache_key: str):
        """检查 GitHub API 限速响应

        返回值：
        - None：未限速，调用者继续处理响应
        - _RATE_LIMITED：已限速，调用者应返回 None（视为"无新版本"）
        """
        is_rate_limited = (
            resp.status_code == 429 or
            (resp.status_code == 403 and resp.headers.get('X-RateLimit-Remaining') == '0')
        )
        if is_rate_limited:
            reset_time = resp.headers.get('X-RateLimit-Reset', '')
            logger.warning(f"GitHub API rate limit hit (HTTP {resp.status_code}). Reset at: {reset_time}")
            # 缓存为 None（无更新），避免短时间重复请求
            self._last_check[cache_key] = (_time.time(), None)
            return self._RATE_LIMITED
        remaining = resp.headers.get('X-RateLimit-Remaining')
        if remaining is not None and int(remaining) <= 5:
            logger.warning(f"GitHub API rate limit nearly exhausted: {remaining} requests remaining")
        return None

    def check_update(self) -> Optional[dict]:
        """检查 Venlta 应用是否有新版本

        返回值：
        - dict: 有新版本时返回版本信息
        - dict（含 ok=False, error）: 检查失败时返回错误信息
        - None: 无新版本
        """
        cache_key = GITHUB_REPO
        if cache_key in self._last_check:
            cached_ts, cached_result = self._last_check[cache_key]
            if _time.time() - cached_ts < _CACHE_TTL:
                return cached_result
        try:
            system = platform.system().lower()
            machine = "amd64" if platform.machine() in ("x86_64", "AMD64") else "arm64"

            etag_headers = {}
            cached_etag = self._etag_cache.get(cache_key, {}).get("etag")
            if cached_etag:
                etag_headers["If-None-Match"] = cached_etag

            resp = self._github_get(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                etag_headers,
            )

            # 记录剩余配额，便于诊断 rate limit 问题
            remaining = resp.headers.get('X-RateLimit-Remaining')
            if remaining is not None:
                logger.debug(f"GitHub API rate limit remaining: {remaining}")

            rate_limit_result = self._check_rate_limit(resp, cache_key)
            if rate_limit_result is self._RATE_LIMITED:
                return None  # 限速时静默返回"无新版本"，不报错

            if resp.status_code == 304:
                logger.debug(f"GitHub API 304 for {cache_key}, reusing cached result")
                cached_entry = self._etag_cache.get(cache_key, {})
                cached_result = cached_entry.get("result")
                self._last_check[cache_key] = (_time.time(), cached_result)
                return cached_result

            if resp.status_code == 404:
                # 仓库不存在或没有发布 release — 不是错误，只是暂无更新
                logger.info(f"No release found for {GITHUB_REPO} (404)")
                result = None  # 视为"无新版本"，不报错
                self._last_check[cache_key] = (_time.time(), result)
                return result

            if resp.status_code != 200:
                result = {"ok": False, "error": f"GitHub API returned {resp.status_code}"}
                self._last_check[cache_key] = (_time.time(), result)
                return result

            new_etag = resp.headers.get("ETag")
            release = resp.json()
            latest = release["tag_name"].lstrip("v")
            if self._compare_versions(latest, self.current_version) <= 0:
                self._last_check[cache_key] = (_time.time(), None)
                if new_etag:
                    self._etag_cache[cache_key] = {"etag": new_etag, "result": None}
                    self._save_etag_cache()
                return None

            asset_name = f"venlta-{system}-{machine}"
            for asset in release.get("assets", []):
                if asset_name in asset["name"]:
                    result = {
                        "version": latest,
                        "download_url": asset["browser_download_url"],
                        "release_notes": release.get("body", ""),
                        "size": asset.get("size", 0),
                    }
                    self._last_check[cache_key] = (_time.time(), result)
                    if new_etag:
                        self._etag_cache[cache_key] = {"etag": new_etag, "result": result}
                        self._save_etag_cache()
                    return result
            err_result = {"ok": False, "error": f"No matching asset for {asset_name}", "has_new_version": True}
            self._last_check[cache_key] = (_time.time(), err_result)
            return err_result
        except Exception as e:
            logger.error(f"Failed to check update: {e}")
            return {"ok": False, "error": str(e)}

    def get_singbox_download_info(self) -> Optional[dict]:
        """获取 sing-box 固定版本 (1.13.13) 的下载信息

        直接拼下载 URL，无需调用 GitHub API（避免限速问题）。
        SHA256 校验值硬编码在 SINGBOX_SHA256 中，无需远程下载 .sha256 文件。

        Returns:
            dict: {version, download_url, expected_sha256} 或 None
        """
        system = platform.system().lower()
        machine = "amd64" if platform.machine() in ("x86_64", "AMD64") else "arm64"

        # Linux: .tar.gz, Windows: .zip
        if system == "windows":
            ext = ".zip"
        else:
            ext = ".tar.gz"

        platform_key = f"{system}-{machine}"
        expected_sha256 = SINGBOX_SHA256.get(platform_key, "")

        asset_name = f"sing-box-{SINGBOX_VERSION}-{system}-{machine}{ext}"
        base_url = f"https://github.com/{SINGBOX_REPO}/releases/download/v{SINGBOX_VERSION}"

        return {
            "version": SINGBOX_VERSION,
            "download_url": f"{base_url}/{asset_name}",
            "expected_sha256": expected_sha256,
        }

    def _get_current_singbox_version(self) -> str:
        """获取当前安装的 sing-box 版本号"""
        try:
            from core.config_manager import find_singbox_binary
            singbox_bin = find_singbox_binary()
            if not singbox_bin:
                return "0.0.0"
            result = subprocess.run(
                [singbox_bin, "version"],
                capture_output=True, text=True, timeout=5,
                close_fds=(platform.system() != "Linux"),
            )
            if result.returncode == 0:
                version_match = re.search(r'(\d+\.\d+\.\d+)', result.stdout.strip())
                return version_match.group(1) if version_match else "0.0.0"
            return "0.0.0"
        except FileNotFoundError:
            logger.warning("sing-box binary not found")
            return "0.0.0"
        except Exception as e:
            logger.warning(f"Failed to get sing-box version: {e}")
            return "0.0.0"

    def is_singbox_installed(self) -> bool:
        """检查 sing-box 核心是否已安装（只检查自身目录，不查系统 PATH）"""
        try:
            from core.config_manager import find_singbox_binary
            binary = find_singbox_binary()
            if not binary:
                return False
            return Path(binary).exists()
        except Exception:
            return False

    def check_updates_on_startup(self, callback=None):
        """启动时检查应用更新（不再检查 sing-box 更新）

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
        if callback:
            callback(result)
        return result

    def install_core_update(self, archive_path: str) -> dict:
        """安装 sing-box 核心（从下载的归档文件）

        从 .tar.gz 或 .zip 归档中提取所有文件（sing-box 二进制及所有依赖），
        扁平化安装到 sing-box 目录。
        替换前会验证新二进制文件能否正常运行。

        Args:
            archive_path: 下载的归档文件路径 (.tar.gz 或 .zip)

        Returns:
            {"ok": True} 成功, {"ok": False, "error": "..."} 失败
        """
        import tarfile
        import zipfile
        import shutil
        import stat

        is_windows = platform.system() == "Windows"
        binary_name = "sing-box.exe" if is_windows else "sing-box"

        try:
            archive = Path(archive_path)
            if not archive.exists():
                return {"ok": False, "error": f"Archive not found: {archive_path}"}

            from core.config_manager import find_singbox_binary, get_singbox_dir
            current_binary = find_singbox_binary()
            if current_binary:
                current_binary = Path(current_binary)
                install_dir = current_binary.parent
            else:
                # 固定安装到 backend/sing-box/ 目录
                install_dir = get_singbox_dir()
                install_dir.mkdir(parents=True, exist_ok=True)

            extract_dir = Path(archive_path).parent / "singbox_update_tmp"
            extract_dir.mkdir(parents=True, exist_ok=True)

            archive_suffix = archive.name.lower()
            extracted_files: list[Path] = []

            if archive_suffix.endswith('.zip'):
                with zipfile.ZipFile(archive_path, 'r') as zf:
                    for entry in zf.namelist():
                        # 跳过目录条目
                        if entry.endswith('/'):
                            continue
                        basename = Path(entry).name
                        # 跳过隐藏文件和 macOS 资源文件
                        if basename.startswith('.') or basename.startswith('_'):
                            continue
                        zf.extract(entry, extract_dir)
                        extracted_src = extract_dir / entry
                        # 扁平化：移到 extract_dir 根级（去掉归档内的子目录）
                        extracted_dst = extract_dir / basename
                        if extracted_src != extracted_dst:
                            if extracted_dst.exists():
                                extracted_dst.unlink()
                            extracted_src.rename(extracted_dst)
                            # 清理空的子目录
                            try:
                                extracted_src.parent.rmdir()
                            except OSError:
                                pass
                        extracted_files.append(extracted_dst)
            elif archive_suffix.endswith('.tar.gz') or archive_suffix.endswith('.tgz'):
                with tarfile.open(archive_path, 'r:gz') as tar:
                    for member in tar.getmembers():
                        if not member.isfile():
                            continue
                        basename = Path(member.name).name
                        # 跳过隐藏文件和 macOS 资源文件
                        if basename.startswith('.') or basename.startswith('_'):
                            continue
                        # 扁平化提取：只保留文件名，去掉归档内的子目录
                        original_name = member.name
                        member.name = basename
                        try:
                            tar.extract(member, extract_dir, filter='data')
                        except TypeError:
                            tar.extract(member, extract_dir)
                        member.name = original_name  # 还原，避免影响后续迭代
                        extracted_files.append(extract_dir / basename)
            else:
                return {"ok": False, "error": f"Unsupported archive format: {archive.name}"}

            # 检查 sing-box 二进制是否成功提取
            extracted_binary = extract_dir / binary_name
            if not extracted_binary.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)
                return {"ok": False, "error": f"{binary_name} not found in archive"}

            # 设置可执行权限（Linux）
            if not is_windows:
                for f in extracted_files:
                    if f.exists():
                        f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

            # 验证新二进制能否正常运行
            try:
                test_result = subprocess.run(
                    [str(extracted_binary), "version"],
                    capture_output=True, text=True, timeout=5,
                    close_fds=(platform.system() != "Linux"),
                )
                if test_result.returncode != 0:
                    logger.error(f"New sing-box binary verification failed: {test_result.stderr}")
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    return {"ok": False, "error": "New binary verification failed"}
            except Exception as verify_err:
                logger.error(f"Cannot verify new sing-box binary: {verify_err}")
                shutil.rmtree(extract_dir, ignore_errors=True)
                return {"ok": False, "error": f"Binary verification error: {verify_err}"}

            # 备份旧文件（所有旧文件，不只是二进制）
            backup_dir = install_dir.parent / f"{install_dir.name}.bak"
            try:
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                if install_dir.exists():
                    shutil.copytree(install_dir, backup_dir)
            except Exception:
                logger.debug("Failed to backup existing sing-box directory")

            # 将所有提取的文件安装到目标目录
            installed_files = []
            for src_file in extracted_files:
                dst_file = install_dir / src_file.name
                try:
                    shutil.copy2(src_file, dst_file)
                    installed_files.append(dst_file)
                    logger.info(f"Installed: {dst_file}")
                except PermissionError:
                    # Windows 上文件可能被锁定，尝试先删除再复制
                    try:
                        dst_file.unlink()
                        shutil.copy2(src_file, dst_file)
                        installed_files.append(dst_file)
                        logger.info(f"Installed (replaced): {dst_file}")
                    except Exception as replace_err:
                        logger.error(f"Failed to install {src_file.name}: {replace_err}")
                except Exception as copy_err:
                    logger.error(f"Failed to copy {src_file.name}: {copy_err}")

            # 清理临时目录和归档文件
            shutil.rmtree(extract_dir, ignore_errors=True)
            try:
                archive.unlink()
            except Exception:
                pass

            # 清理备份目录（安装成功后）
            try:
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
            except Exception:
                pass

            # 最终验证：确认关键文件都已就位
            final_binary = install_dir / binary_name
            if not final_binary.exists():
                # 尝试从备份恢复
                if backup_dir.exists():
                    shutil.copytree(backup_dir, install_dir, dirs_exist_ok=True)
                return {"ok": False, "error": f"sing-box binary not found after installation"}

            return {"ok": True, "installed_files": [str(f) for f in installed_files]}

        except Exception as e:
            logger.error(f"Failed to install core update: {e}")
            return {"ok": False, "error": str(e)}

    def install_app_update(self, archive_path: str) -> dict:
        """安装应用更新（暂未实现）"""
        return {"ok": False, "error": "App auto-install is not yet supported. Please install the update manually."}

    def download_and_verify(self, url: str, sha256_url: str = "", expected_sha256: str = "") -> Optional[Path]:
        """下载更新包并验证 SHA256

        支持两种校验方式（互斥，优先 expected_sha256）：
        - expected_sha256: 直接传入预知的 SHA256 哈希值（硬编码）
        - sha256_url: 从远程下载 .sha256 校验文件

        Args:
            url: 下载链接
            sha256_url: SHA256 校验文件链接（可选）
            expected_sha256: 预期 SHA256 哈希值（可选，优先于 sha256_url）

        Returns:
            下载文件路径；校验失败返回 None
        """
        try:
            url_lower = url.lower()
            if url_lower.endswith('.tar.gz'):
                suffix = '.tar.gz'
            elif url_lower.endswith('.tgz'):
                suffix = '.tgz'
            else:
                suffix = '.zip'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                with httpx.stream("GET", url, timeout=120, follow_redirects=True) as resp:
                    for chunk in resp.iter_bytes(chunk_size=8192):
                        tmp.write(chunk)
                tmp_path = Path(tmp.name)

            # SHA256 校验
            if expected_sha256:
                # 方式 1：使用硬编码的 SHA256 值校验
                actual_hash = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
                if actual_hash != expected_sha256:
                    logger.error(f"SHA256 mismatch: expected {expected_sha256}, got {actual_hash}")
                    tmp_path.unlink()
                    return None
                logger.info(f"Download verified: {tmp_path} (SHA256 match)")
            elif sha256_url:
                # 方式 2：从远程下载 .sha256 文件校验
                sha256_resp = httpx.get(sha256_url, timeout=10, follow_redirects=True)
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
            else:
                logger.info(f"Download complete (no SHA256 verification): {tmp_path}")

            return tmp_path
        except Exception as e:
            logger.error(f"Download/verify failed: {e}")
            return None
