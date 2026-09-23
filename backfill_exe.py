"""backfill_exe.py — 一次性补数据: 给历史会话回填 exe 路径.

`exe_path` 列是后加的 (为了取应用图标), 之前记录的会话这一列是空的.
这个脚本从**当前正在运行的进程**里按"友好名"认出 exe 路径, 补回数据库,
这样管理页面对历史数据也能显示图标.

用法:
    .venv\\Scripts\\python.exe backfill_exe.py            # 只补, 只读进程列表
"""
from __future__ import annotations

import os
import sys

import psutil

import db
from tracker_worker import NAME_MAP, file_description


def scan_running() -> dict:
    """{友好名: exe 路径} —— 从当前进程里建映射 (同名只留第一个)."""
    found: dict[str, str] = {}
    for p in psutil.process_iter(["name", "exe"]):
        try:
            info = p.info
            exe = info.get("exe")
            name = info.get("name") or ""
            if not exe or not os.path.isfile(exe):
                continue
            friendly = file_description(exe) or NAME_MAP.get(name, name)
            if friendly and friendly not in found:
                found[friendly] = exe
        except Exception:
            continue
    return found


def main() -> int:
    db.init_db()
    print("扫描正在运行的进程…")
    running = scan_running()
    print(f"  认出 {len(running)} 个应用\n")

    with db._connect() as conn:
        rows = conn.execute(
            "SELECT process, COUNT(*) AS n FROM sessions "
            "WHERE exe_path IS NULL GROUP BY process ORDER BY n DESC"
        ).fetchall()

    if not rows:
        print("没有需要回填的记录 (全部已有 exe_path)。")
        return 0

    fixed_names = 0
    fixed_rows = 0
    with db._connect() as conn:
        for r in rows:
            proc = r["process"]
            exe = running.get(proc)
            if not exe:
                print(f"  [跳过] {proc}  ({r['n']} 段) —— 现在没在跑, 认不出来")
                continue
            cur = conn.execute(
                "UPDATE sessions SET exe_path = ? WHERE process = ? AND exe_path IS NULL",
                (exe, proc),
            )
            fixed_rows += cur.rowcount or 0
            fixed_names += 1
            print(f"  [OK]   {proc}  ({r['n']} 段) -> {exe}")

    print(f"\n完成: {fixed_names} 个应用 / {fixed_rows} 条会话已回填。")
    print("现在没在跑的应用, 等它下次运行一次就会自动带上 exe 路径。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
