"""Shared, user-editable application settings stored beside the executable."""
from __future__ import annotations

import json
import os
import tempfile
import threading

import paths

SETTINGS_PATH = os.path.join(paths.app_dir(), "data", "hud_config.json")
_LOCK = threading.RLock()

FEATURE_DEFAULTS = {
    "show_proxy_location": False,
    "show_mouse_battery": True,
    "show_download_progress": False,
    "download_progress_dir": "",
}


def get_settings() -> dict:
    with _LOCK:
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        result = dict(FEATURE_DEFAULTS)
        for key in FEATURE_DEFAULTS:
            if key not in data:
                continue
            if key == "download_progress_dir":
                result[key] = data[key] if isinstance(data[key], str) else ""
            elif isinstance(data[key], bool):
                result[key] = data[key]
        return result


def update_settings(updates: dict) -> dict:
    clean = {}
    for key in FEATURE_DEFAULTS:
        if key not in updates:
            continue
        if key == "download_progress_dir":
            clean[key] = str(updates[key] or "").strip()
        else:
            if not isinstance(updates[key], bool):
                raise ValueError(f"{key} must be boolean")
            clean[key] = updates[key]

    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with _LOCK:
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data.update(clean)
        fd, tmp = tempfile.mkstemp(prefix="hud_config.", suffix=".tmp",
                                   dir=os.path.dirname(SETTINGS_PATH))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SETTINGS_PATH)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return get_settings()


def feature_enabled(name: str) -> bool:
    return bool(get_settings().get(name, FEATURE_DEFAULTS.get(name, False)))
