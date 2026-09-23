"""生成今日模拟数据, 用于测试 dashboard."""
import os
import sys

# 切到脚本目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
from datetime import datetime

db.init_db()

now = datetime.now()
today = now.strftime("%Y-%m-%d")

sessions = [
    ("08:00:00", "09:30:00", "Visual Studio Code", "概率论作业.py - VS Code"),
    ("09:30:00", "10:00:00", "Google Chrome", "YouTube - 数学公开课"),
    ("10:00:00", "12:00:00", "MATLAB", "Untitled - MATLAB R2024a"),
    ("12:00:00", "13:00:00", "WeChat", "微信"),
    ("13:00:00", "15:30:00", "Steam", "Elden Ring"),
    ("15:30:00", "16:00:00", "WeGame", "League of Legends"),
    ("16:00:00", "18:00:00", "Adobe Photoshop", "海报设计.psd"),
    ("18:00:00", "19:00:00", "抖音", "抖音"),
    ("19:00:00", "21:00:00", "Blender", "穿越机.blend - Blender"),
    ("21:00:00", "23:00:00", "Microsoft Edge", "GitHub - 0xarchit/focusd"),
]

for s, e, p, w in sessions:
    db.insert_session(f"{today} {s}", f"{today} {e}", p, w)

print(f"[seed] inserted {len(sessions)} sessions into {db.DB_PATH}")

import json
ov = db.dashboard_overview()
print(f"[seed] overview: today={ov['today_seconds']:.0f}s, week={ov['week_seconds']:.0f}s, month={ov['month_seconds']:.0f}s")
