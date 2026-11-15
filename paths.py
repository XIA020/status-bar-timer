"""paths.py — 统一解析资源目录 / 数据目录 / 子进程命令行.

源码运行 和 PyInstaller 打包后运行 的路径规则不一样, 全部收在这里, 避免每个文件各写一套.

* `res_dir()`  只读资源 (dashboard.html/css/js, tray.ps1) —— 打包后在 _MEIPASS 里
* `app_dir()`  可写目录 (data/, 日志) —— 打包后使用用户的 LocalAppData

子进程: 打包后 `sys.executable` 就是 exe 本身, 所以子进程不能用 "pythonw 脚本" 那种方式起,
改成同一个 exe 带 `--role <角色>`, 由 app.py 里的分发函数处理.
"""
from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def res_dir() -> str:
    """只读资源目录."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def app_dir() -> str:
    """可写目录 (data / 日志 / 缓存)."""
    if is_frozen():
        local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(local, "StatusBarTimer")
    return os.path.dirname(os.path.abspath(__file__))


def migrate_legacy_data() -> None:
    """首次升级时导入 EXE 旁边的旧数据。调用方须持有主程序互斥锁。"""
    if not is_frozen():
        return
    from contextlib import closing
    from pathlib import Path
    import shutil
    import sqlite3
    from tempfile import TemporaryDirectory

    target = Path(app_dir()) / "data"
    if target.exists():
        return
    legacy = Path(sys.executable).resolve().parent / "data"
    settings = [name for name in ("hud_config.json", "hud_pos.json", "battery.json")
                if (legacy / name).is_file()]
    database = legacy / "screen_time.db"
    if not settings and not database.is_file():
        return
    # 先完成整个导入再提交，失败时可重试。SQLite 备份保留尚在 WAL 中的记录。
    with TemporaryDirectory(prefix="migration-", dir=target.parent) as tmp:
        staged = Path(tmp) / "data"
        staged.mkdir()
        for name in settings:
            shutil.copyfile(legacy / name, staged / name)
        if database.is_file():
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(staged / database.name)) as destination:
                    source.backup(destination)
        staged.rename(target)


def child_cmd(role: str, script: str) -> list[str]:
    """构造子进程命令行.

    源码模式: 用 venv 的 pythonw 跑脚本 (无控制台窗口)
    打包模式: 还是这个 exe, 带 --role
    """
    if is_frozen():
        return [sys.executable, "--role", role]
    root = app_dir()
    pythonw = os.path.join(root, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return [pythonw, os.path.join(root, script)]


def child_env() -> dict[str, str]:
    """Give each long-lived onefile child its own extraction directory."""
    env = os.environ.copy()
    if is_frozen():
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env
