"""从 GitHub Release 检查并下载用户选择的更新。"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import paths
from release_info import OWNER, REPO, VERSION, asset_name

_LATEST = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
_DOWNLOADS = f"https://github.com/{OWNER}/{REPO}/releases/download"
_GITHUB_HOSTS = {"api.github.com", "github.com", "release-assets.githubusercontent.com",
                 "objects.githubusercontent.com"}
_MAX_ASSET_SIZE = 128 * 1024 * 1024
_CHECK_SECONDS = 24 * 60 * 60


class _ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if url.scheme != "https" or url.hostname not in _GITHUB_HOSTS or url.port not in (None, 443):
            raise ValueError("发布附件重定向地址无效")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    size: int
    sha256: str


def _read_release(payload: dict) -> Release | None:
    tag = payload.get("tag_name")
    if payload.get("draft") or payload.get("prerelease") or not isinstance(tag, str):
        return None
    if not re.fullmatch(r"v[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}", tag):
        return None
    version = tag[1:]
    if tuple(map(int, version.split("."))) <= tuple(map(int, VERSION.split("."))):
        return None

    name = asset_name(tag)
    canonical_url = f"{_DOWNLOADS}/{tag}/{name}"
    for asset in payload.get("assets", ()):
        size, digest = asset.get("size"), asset.get("digest")
        if (asset.get("name") == name and asset.get("state") == "uploaded"
                and asset.get("browser_download_url") == canonical_url
                and type(size) is int and 0 < size <= _MAX_ASSET_SIZE
                and isinstance(digest, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", digest)):
            return Release(version, canonical_url, size, digest[7:])
    raise ValueError("新版没有可验证的 Windows 下载附件")


def _request(url: str, accept: str):
    request = Request(url, headers={"User-Agent": f"StatusBarTimer/{VERSION}", "Accept": accept})
    return build_opener(_ReleaseRedirect()).open(request, timeout=12)


def _find_latest() -> Release | None:
    with _request(_LATEST, "application/vnd.github+json") as response:
        return _read_release(json.loads(response.read(1024 * 1024)))


def _download(release: Release, directory: Path) -> Path:
    """在目标盘创建暂存文件，校验通过后才允许旧程序退出。"""
    stage = None
    try:
        with NamedTemporaryFile(prefix=".timer-update-", suffix=".tmp", dir=directory,
                                delete=False) as output:
            stage = Path(output.name)
            digest = hashlib.sha256()
            count = 0
            deadline = time.monotonic() + 180
            with _request(release.url, "application/octet-stream") as response:
                while chunk := response.read(256 * 1024):
                    count += len(chunk)
                    if count > release.size or time.monotonic() > deadline:
                        raise ValueError("下载大小异常或超过时限")
                    digest.update(chunk)
                    output.write(chunk)
            if count != release.size or digest.hexdigest() != release.sha256:
                raise ValueError("更新文件校验不通过")
        return stage
    except Exception:
        if stage is not None:
            stage.unlink(missing_ok=True)
        raise


def _launch_installer(powershell: str, stage: Path, target: Path, digest: str) -> None:
    script = (Path(paths.res_dir()) / "install_update.ps1").read_text(encoding="utf-8-sig")
    command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    env = paths.child_env()
    env.update(TIMER_UPDATE_STAGE=str(stage), TIMER_UPDATE_TARGET=str(target),
               TIMER_UPDATE_SHA256=digest, TIMER_UPDATE_PARENT=str(os.getpid()))
    subprocess.Popen(
        [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
        cwd=paths.app_dir(), env=env, creationflags=0x08000000 | 0x00000200,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


class Updater:
    """托盘状态和后台任务由应用进程持有。"""

    def __init__(self, powershell, quit_app):
        self._powershell = powershell
        self._quit_app = quit_app
        self._lock = threading.Lock()
        self._release = None
        self._phase = "idle"
        self._notice = ""
        self._next_check = time.monotonic() + 8
        self._skip_path = Path(paths.app_dir()) / "data" / "update-dismissed.txt"
        try:
            self._dismissed = self._skip_path.read_text(encoding="utf-8").strip()
        except OSError:
            self._dismissed = ""

    def menu_state(self):
        with self._lock:
            version = self._release.version if self._release else ""
            return {"current": VERSION, "version": version, "phase": self._phase,
                    "prompt": bool(version and version != self._dismissed and self._phase == "available"),
                    "notice": self._notice}

    def check(self, *, manual=False):
        with self._lock:
            if self._phase in ("checking", "downloading"):
                return
            now = time.monotonic()
            if not manual and now < self._next_check:
                return
            self._next_check = now + _CHECK_SECONDS
            self._phase, self._notice = "checking", ""
        threading.Thread(target=self._check, args=(manual,), daemon=True, name="update-check").start()

    def _check(self, manual):
        try:
            release = _find_latest()
            notice = "当前已是最新版本。" if manual and release is None else ""
        except Exception as exc:
            print(f"[update] check failed: {exc}", file=sys.stderr, flush=True)
            with self._lock:
                self._phase = "available" if self._release else "idle"
                self._notice = "暂时无法检查更新，请稍后重试。" if manual else ""
            return
        with self._lock:
            self._release = release
            self._phase, self._notice = ("available" if release else "idle"), notice

    def dismiss(self, version):
        with self._lock:
            if self._release and version == self._release.version:
                self._dismissed = version
                try:
                    self._skip_path.write_text(version, encoding="utf-8")
                except OSError:
                    pass

    def install(self, version):
        with self._lock:
            release = self._release
            if self._phase != "available" or not release or release.version != version:
                return
            self._phase, self._notice = "downloading", ""
        threading.Thread(target=self._install, args=(release,), daemon=True, name="update-install").start()

    def _install(self, release):
        stage = None
        try:
            target = Path(sys.executable).resolve()
            stage = _download(release, target.parent)
            _launch_installer(self._powershell(), stage, target, release.sha256)
        except Exception as exc:
            if stage:
                try:
                    stage.unlink(missing_ok=True)
                except OSError:
                    pass
            print(f"[update] download or launch failed: {exc}", file=sys.stderr, flush=True)
            with self._lock:
                self._phase = "available"
                self._notice = "更新未完成。当前版本仍可使用，可从托盘重试。"
            return
        self._quit_app()
