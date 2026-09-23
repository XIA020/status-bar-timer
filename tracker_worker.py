"""tracker_worker.py — 单独进程: 轮询前台窗口 + 写 SQLite.

不依赖 Win32 GUI 线程, 跟 app.py 的 socket server 完全隔离.
通过两个 JSON 文件做 IPC:
    data/state.json   ← tracker 写: 当前前台 / running / paused / 今日累计 / Top 应用
    data/control.json ← app.py 写: 控制命令 (paused=1/0, shutdown=1/0)

"时间叠加": 每个应用今天已用多久记在内存基数 base 里, 切走时把当前段累加进去,
切回来就从基数继续往上走 (不是从 0 重来). base 在启动时和跨零点时从 DB 重载.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

import db
import paths

ROOT = paths.app_dir()
DATA_DIR = os.path.join(ROOT, "data")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
CONTROL_PATH = os.path.join(DATA_DIR, "control.json")

CHECK_INTERVAL = 1.0  # 秒


def log(msg):
    try:
        with open(os.path.join(DATA_DIR, "tracker_debug.log"), "a", encoding="utf-8") as f:
            f.write(f"[tracker_worker] {msg}\n")
    except Exception:
        pass


def file_description(path):
    if not path or not os.path.exists(path):
        return None
    try:
        import win32api
        info = win32api.GetFileVersionInfo(path, "\\")
        translations = win32api.VerQueryValue(info, r"\VarFileInfo\Translation") or []
        for lang, codepage in translations:
            for key in ("FileDescription", "ProductName"):
                sub = rf"\StringFileInfo\{lang:04X}{codepage:04X}\{key}"
                v = win32api.VerQueryValue(info, sub)
                if v:
                    s = str(v).strip()
                    if s:
                        return s
    except Exception:
        pass
    return None


NAME_MAP = {
    "Code.exe": "Visual Studio Code", "msedge.exe": "Microsoft Edge",
    "chrome.exe": "Google Chrome", "firefox.exe": "Mozilla Firefox",
    "explorer.exe": "File Explorer",
    "python.exe": "Python", "pythonw.exe": "Python", "python3.exe": "Python",
    "WeGame.exe": "WeGame", "steam.exe": "Steam",
    "EpicGamesLauncher.exe": "Epic Games", "UbisoftConnect.exe": "Ubisoft Connect",
    "Origin.exe": "EA", "Battle.net.exe": "Battle.net",
    "notepad.exe": "Notepad", "blender.exe": "Blender",
    "Photoshop.exe": "Adobe Photoshop", "Lightroom.exe": "Adobe Lightroom",
    "Premiere Pro.exe": "Adobe Premiere", "AfterFX.exe": "Adobe After Effects",
    "MATLAB.exe": "MATLAB", "WeChat.exe": "WeChat", "QQ.exe": "QQ",
    "douyin.exe": "抖音", "vlc.exe": "VLC", "PotPlayerMini.exe": "PotPlayer",
    "obs64.exe": "OBS Studio", "Discord.exe": "Discord",
    "DevToolsAppX.exe": "Edge WebView2",
}


def get_active_window():
    """返回 (友好名, 窗口标题, exe 全路径). 失败返回 (None, None, None)."""
    try:
        import win32gui
        import win32process
        import psutil
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None, None, None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not pid:
            return None, None, None
        try:
            proc = psutil.Process(pid)
            exe = proc.exe()
        except Exception:
            exe = None
        friendly = file_description(exe) if exe else None
        if not friendly:
            try:
                name = proc.name()
            except Exception:
                name = "(unknown)"
            friendly = NAME_MAP.get(name, name)
        try:
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            title = ""
        return friendly, title, exe
    except Exception as e:
        log(f"get_active_window err: {e}")
        return None, None, None


def read_control():
    try:
        with open(CONTROL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"paused": False, "shutdown": False}


def write_state(state):
    """原子写: 写 tmp + rename, 避免读端读到半截 JSON.

    ⚠️ Windows 上 os.replace 需要目标文件可被删除, 而读端(HUD 每 500ms 读一次)
    用普通 open() 读时没有 FILE_SHARE_DELETE, 撞上就会报 WinError 5 拒绝访问.
    概率很低但不是零, 所以这里重试几次.
    """
    tmp = STATE_PATH + ".tmp"
    try:
        payload = json.dumps(state, ensure_ascii=False)
    except Exception as e:
        log(f"write_state encode err: {e}")
        return
    for attempt in range(5):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, STATE_PATH)
            return
        except PermissionError:
            time.sleep(0.02 * (attempt + 1))
        except Exception as e:
            log(f"write_state err: {e}")
            return
    log("write_state: 重试 5 次仍失败 (有别的进程一直占着 state.json?)")


def fmt_duration(seconds):
    """人类可读时长: 45s / 2m 5s / 1h 34m.

    ⚠️ `divmod(s, 3600)` 返回的是 (小时, **剩余秒数**), 不是 (小时, 分钟).
    早先误当成分钟, 于是 5610 秒被显示成 "1h 2010m". 低于 1 小时时不会触发,
    所以只有累计到超过一小时才暴露。
    """
    s = max(0, int(seconds))
    if s >= 3600:
        h, rem = divmod(s, 3600)
        return f"{h}h {rem // 60}m"
    m, sec = divmod(s, 60)
    if m > 0:
        return f"{m}m {sec}s"
    return f"{sec}s"


def fmt_compact(seconds):
    """紧凑格式, 给任务栏那一行 Top 列表用 (省宽度): 45s / 12m / 2h3m."""
    s = max(0, int(seconds))
    if s >= 3600:
        h, rem = divmod(s, 3600)
        m = rem // 60
        return f"{h}h{m}m" if m else f"{h}h"
    if s >= 60:
        return f"{s // 60}m"
    return f"{s}s"


def load_today_totals():
    """从 DB 读今天的每个应用累计秒数 (不含还没落库的当前段)."""
    try:
        return dict(db.query_today_totals())
    except Exception as e:
        log(f"load_today_totals err: {e}")
        return {}


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    db.init_db()
    log(f"started, control={CONTROL_PATH}")

    current = {"process": None, "window": None, "exe": None, "started_at": None}
    paused = False
    last_write = 0.0

    # 今日累计基数 (不含还没落库的当前段).
    # 切走再切回来时, 计时从这个基数往上继续, 而不是从 0 重来 —— 这就是"时间叠加".
    today_key = datetime.now().strftime("%Y-%m-%d")
    base = load_today_totals()
    log(f"today totals loaded: {len(base)} apps, today={today_key}")

    def flush_current(end_ts: float):
        """把当前段落库, 同时把这段时长累加进 base."""
        proc = current["process"]
        started = current["started_at"]
        if not proc or not started:
            return
        seg = max(0.0, end_ts - started)
        try:
            db.insert_session(
                datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
                datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S"),
                proc,
                current["window"] or "",
                current.get("exe"),
            )
        except Exception as e:
            log(f"insert_session err: {e}")
        base[proc] = base.get(proc, 0.0) + seg

    while True:
        # 1) 控制命令
        try:
            ctrl = read_control()
        except Exception:
            ctrl = {}

        if ctrl.get("shutdown"):
            log("shutdown requested")
            flush_current(time.time())
            break

        new_paused = bool(ctrl.get("paused", False))

        # pause 切换时, 把当前段 flush
        if new_paused and not paused:
            flush_current(time.time())
            current = {"process": None, "window": None, "exe": None, "started_at": None}

        paused = new_paused

        # 过零点: 今日累计要从 DB 重新载入, 否则昨天的会算到今天头上
        day_now = datetime.now().strftime("%Y-%m-%d")
        if day_now != today_key:
            log(f"cross midnight {today_key} -> {day_now}, reload today totals")
            today_key = day_now
            base = load_today_totals()

        # 2) 采集
        if not paused:
            try:
                proc, win, exe = get_active_window()
                now = time.time()
                switched = (proc != current["process"]) or (win != current["window"])
                if switched:
                    flush_current(now)
                    current = {"process": proc, "window": win, "exe": exe,
                               "started_at": now if proc else None}
            except Exception as e:
                log(f"loop err: {e}")

        # 3) 写状态 (每 1s)
        now = time.time()
        if now - last_write >= 1.0:
            live = 0.0
            if current["process"] and not paused:
                live = max(0.0, now - (current["started_at"] or now))

            # 今日累计 = 已落库的 + 当前这一段
            totals = dict(base)
            cur = current["process"] if not paused else None
            if cur:
                totals[cur] = totals.get(cur, 0.0) + live

            top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:8]
            state = {
                "running": True,
                "paused": paused,
                "process": cur,
                # 不写 window 字段: PowerShell/IDE 窗口 title 经常被脚本改, 写回 state.json 后
                # 下次读会污染累积. HUD/tBAR 显示只需要 process + duration.
                "duration": fmt_duration(live),           # 本次连续时长
                "today_total": fmt_duration(totals.get(cur, 0.0)) if cur else "0s",  # 今日累计
                "top_apps": [
                    {
                        "name": p,
                        "seconds": round(s, 1),
                        "time": fmt_compact(s),           # 45s / 12m / 2h3m
                        "time_long": fmt_duration(s),
                    }
                    for p, s in top
                ],
                "updated_at": now,
            }
            write_state(state)
            last_write = now

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
