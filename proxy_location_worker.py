"""通过 Windows 已启用的系统代理查询出口国家代码，供 HUD 显示节点旗帜。"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

import paths
import app_settings

ROOT = paths.app_dir()
DATA_DIR = os.path.join(ROOT, "data")
STATE_PATH = os.path.join(DATA_DIR, "proxy_location.json")
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
POLL_SECONDS = 120


def _proxy_map() -> dict[str, str] | None:
    """只读 WinINet 系统代理；系统代理未启用时不发起查询。"""
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if not enabled or not str(server).strip():
            return None
        raw = str(server).strip()
        entries = {}
        if "=" in raw:
            for part in re.split(r"[;,]", raw):
                if "=" in part:
                    name, value = part.split("=", 1)
                    entries[name.strip().lower()] = value.strip()
        else:
            entries = {"http": raw, "https": raw}

        def as_url(value: str) -> str:
            return value if "://" in value else "http://" + value

        proxy = entries.get("https") or entries.get("http") or entries.get("socks")
        if not proxy:
            return None
        uri = as_url(proxy)
        return {"http": uri, "https": uri}
    except Exception:
        return None


def read_country() -> str | None:
    proxy = _proxy_map()
    if not proxy:
        return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy))
    request = urllib.request.Request(TRACE_URL, headers={"User-Agent": "StatusBarTimer/1.0"})
    with opener.open(request, timeout=8) as response:
        body = response.read(8192).decode("utf-8", errors="replace")
    for line in body.splitlines():
        if line.startswith("loc="):
            country = line[4:].strip().upper()
            if re.fullmatch(r"[A-Z]{2}", country):
                return country
    return None


def _write_state(state: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)


def run() -> None:
    while True:
        if not app_settings.feature_enabled("show_proxy_location"):
            try:
                _write_state({"available": False, "disabled": True,
                              "updated_at": time.time()})
            except Exception:
                pass
            time.sleep(1)
            continue
        started = time.monotonic()
        try:
            country = read_country()
            _write_state({"available": bool(country), "country": country,
                          "updated_at": time.time()})
        except Exception:
            _write_state({"available": False, "country": None,
                          "updated_at": time.time()})
        time.sleep(max(1, POLL_SECONDS - (time.monotonic() - started)))
