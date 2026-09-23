"""paths.py — 统一解析资源目录 / 数据目录 / 子进程命令行.

源码运行 和 PyInstaller 打包后运行 的路径规则不一样, 全部收在这里, 避免每个文件各写一套.

* `res_dir()`  只读资源 (dashboard.html/css/js, tray.ps1) —— 打包后在 _MEIPASS 里
* `app_dir()`  可写目录 (data/, 日志) —— 打包后就在 exe 旁边,
               这样把 exe 放进项目目录时, 跟源码版共用同一份历史数据

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
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


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
