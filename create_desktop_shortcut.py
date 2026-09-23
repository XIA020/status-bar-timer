"""create_desktop_shortcut.py — 在桌面创建「状态栏记时器」快捷方式.

直接用 pywin32 的 WScript.Shell COM 创建 .lnk, 不再拼 PowerShell 命令串
(路径含中文, 拼字符串过 PowerShell 容易出现编码问题).

路径全部按脚本自身位置推导, 所以项目整体搬目录之后不用改这里.
"""
from __future__ import annotations

import os
import sys

import win32com.client

PROJ = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(PROJ, "状态栏记时器.exe")
START_CMD = os.path.join(PROJ, "start.cmd")
ICON = os.path.join(PROJ, "tray_icon.ico")
NAME = "状态栏记时器.lnk"


def desktop_dir() -> str:
    """"桌面" 的真实路径 (OneDrive 重定向也能拿对)."""
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            p = winreg.QueryValueEx(k, "Desktop")[0]
        if p and os.path.isdir(p):
            return p
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def main() -> int:
    # 优先指向打包好的 exe (自包含, 不依赖 venv); 没有 exe 才退回 start.cmd
    if os.path.exists(EXE):
        target, kind = EXE, "exe"
    elif os.path.exists(START_CMD):
        target, kind = START_CMD, "start.cmd (源码模式)"
    else:
        print(f"[error] 既没有 {EXE} 也没有 {START_CMD}")
        return 1

    lnk = os.path.join(desktop_dir(), NAME)
    ws = win32com.client.Dispatch("WScript.Shell")
    sc = ws.CreateShortcut(lnk)
    sc.TargetPath = target
    sc.WorkingDirectory = PROJ
    sc.WindowStyle = 7          # 最小化启动 (exe 是无控制台程序, 这个只影响 cmd 模式)
    sc.Description = "状态栏记时器 - 任务栏计时 + 屏幕使用时长"
    if os.path.exists(ICON):
        sc.IconLocation = ICON
    else:
        print(f"[warn] 图标不存在, 用默认图标: {ICON}")
    sc.Save()

    print(f"[ok] 快捷方式: {lnk}")
    print(f"     目标    : {target}   [{kind}]")
    print(f"     工作目录: {PROJ}")
    print(f"     图标    : {ICON if os.path.exists(ICON) else '(默认)'}")

    # 顺手清掉上一版留下的、指向旧路径的快捷方式
    old = os.path.join(desktop_dir(), "Screen Time Tracker.lnk")
    if os.path.exists(old):
        print(f"[note] 旧快捷方式仍在: {old}")
        print("       它指向已搬走的旧目录, 已经失效, 可以手动删掉或跑 --clean-old")
        if "--clean-old" in sys.argv:
            os.replace(old, old + ".bak")
            print(f"[ok] 已重命名为 {old}.bak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
