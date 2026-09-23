"""SQLite 数据层：会话记录 + 查询函数.

Schema:
    sessions(id, start_ts, end_ts, process, window_title)
    一个"会话" = 某个前台窗口从 start_ts 到 end_ts 的连续时长.
    当前台窗口变化时, 把上一段写入 DB, 新一段开始累积.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import paths

DB_PATH = os.path.join(paths.app_dir(), "data", "screen_time.db")


def _ensure_data_dir() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def _connect():
    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """初始化 schema. 幂等, 多次调用安全."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_ts TEXT NOT NULL,
                end_ts TEXT NOT NULL,
                process TEXT NOT NULL,
                window_title TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_ts);
            CREATE INDEX IF NOT EXISTS idx_sessions_process ON sessions(process);
        """)
        # 迁移: 老库没有 exe_path 列. 有了它才能取应用图标.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "exe_path" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN exe_path TEXT")
            print("[db] migrated: added sessions.exe_path")


def insert_session(start_ts: str, end_ts: str, process: str, window_title: str,
                   exe_path: str | None = None) -> None:
    """写入一段会话. 末尾时刻必须大于起始时刻."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions(start_ts, end_ts, process, window_title, exe_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (start_ts, end_ts, process, window_title or "", exe_path or None),
        )


# ---------- 查询 ----------

def _seconds_sql(col_start: str, col_end: str) -> str:
    """SQLite 计算两时间字符串(YYYY-MM-DD HH:MM:SS)相差秒数."""
    return f"((julianday({col_end}) - julianday({col_start})) * 86400.0)"


def _today_range() -> tuple[str, str]:
    """今天本地时间的 [start, end) 闭开区间. ISO 字符串."""
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    return today, tomorrow


def _week_range() -> tuple[str, str]:
    """本周一到现在 (周一为一周开始)."""
    now = datetime.now()
    monday = now - timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d")


def _month_range() -> tuple[str, str]:
    """本月第一天到现在."""
    now = datetime.now()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d")


def _n_days_range(n: int) -> tuple[str, str]:
    """过去 N 天 (含今天)."""
    now = datetime.now()
    start = now - timedelta(days=n - 1)
    return start.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d"), \
           (now + timedelta(days=1)).strftime("%Y-%m-%d")


def _day_range(days_ago: int = 0) -> tuple[str, str]:
    """某一天的 [start, end) 区间. days_ago=0 是今天, 1 是昨天."""
    d = (datetime.now() - timedelta(days=days_ago))
    return d.strftime("%Y-%m-%d"), (d + timedelta(days=1)).strftime("%Y-%m-%d")


def _last_week_range() -> tuple[str, str]:
    """上周一 ~ 本周一 (给"比上周"用)."""
    now = datetime.now()
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return (monday - timedelta(days=7)).strftime("%Y-%m-%d"), monday.strftime("%Y-%m-%d")


def query_range_total(start: str, end: str) -> float:
    """任意区间的总秒数."""
    sec = _seconds_sql("start_ts", "end_ts")
    with _connect() as conn:
        row = conn.execute(
            f"SELECT COALESCE(SUM({sec}), 0) AS total FROM sessions "
            f"WHERE start_ts >= ? AND start_ts < ?",
            (start, end),
        ).fetchone()
        return float(row["total"] or 0.0)


def query_hourly_between(start: str, end: str) -> list[float]:
    """区间内按小时聚合的秒数 (只返回 0-23 槽位, 用于一天)."""
    sec = _seconds_sql("start_ts", "end_ts")
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT CAST(substr(start_ts, 12, 2) AS INTEGER) AS hour,
                   SUM({sec}) AS total_seconds
            FROM sessions
            WHERE start_ts >= ? AND start_ts < ?
            GROUP BY hour
            """,
            (start, end),
        ).fetchall()
        slots = [0.0] * 24
        for r in rows:
            h = r["hour"]
            if isinstance(h, int) and 0 <= h < 24:
                slots[h] = float(r["total_seconds"] or 0.0)
        return slots


def query_top_apps_between(start: str, end: str, limit: int = 30) -> list[dict]:
    """按 process 聚合 (带一个代表性 exe_path, 给图标用)."""
    sec = _seconds_sql("start_ts", "end_ts")
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT process,
                   SUM({sec}) AS total_seconds,
                   COUNT(*) AS sessions,
                   MAX(exe_path) AS exe_path
            FROM sessions
            WHERE start_ts >= ? AND start_ts < ?
            GROUP BY process
            ORDER BY total_seconds DESC
            LIMIT ?
            """,
            (start, end, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def query_top_apps(start: str, end: str, limit: int = 20) -> list[dict]:
    """按 process 聚合, 返回 [{process, total_seconds, sessions}]."""
    sec = _seconds_sql("start_ts", "end_ts")
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT process,
                   SUM({sec}) AS total_seconds,
                   COUNT(*) AS sessions
            FROM sessions
            WHERE start_ts >= ? AND start_ts < ?
            GROUP BY process
            ORDER BY total_seconds DESC
            LIMIT ?
            """,
            (start, end, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def query_daily_total(start: str, end: str) -> list[dict]:
    """按日期聚合总秒数. 用于趋势图."""
    sec = _seconds_sql("start_ts", "end_ts")
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT substr(start_ts, 1, 10) AS day,
                   SUM({sec}) AS total_seconds
            FROM sessions
            WHERE start_ts >= ? AND start_ts < ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (start, end),
        ).fetchall()
        return [dict(r) for r in rows]


def query_hourly_today() -> list[dict]:
    """今日按小时 (0-23) 聚合秒数. 用于时段热力图."""
    sec = _seconds_sql("start_ts", "end_ts")
    today, tomorrow = _today_range()
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT CAST(substr(start_ts, 12, 2) AS INTEGER) AS hour,
                   SUM({sec}) AS total_seconds
            FROM sessions
            WHERE start_ts >= ? AND start_ts < ?
            GROUP BY hour
            ORDER BY hour ASC
            """,
            (today, tomorrow),
        ).fetchall()
        # 填满 0-23, 缺失小时补 0
        result = {h: 0.0 for h in range(24)}
        for r in rows:
            result[r["hour"]] = r["total_seconds"]
        return [{"hour": h, "total_seconds": result[h]} for h in range(24)]


def query_today_totals() -> dict:
    """今日每个应用的累计秒数 {process: seconds}. 给任务栏 HUD 的"今日累计"用.

    注意: 只统计 start_ts 落在今天的会话. 跨零点的会话整段算给前一天
    (跟 dashboard 的口径一致).
    """
    sec = _seconds_sql("start_ts", "end_ts")
    today, tomorrow = _today_range()
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT process, SUM({sec}) AS total_seconds
            FROM sessions
            WHERE start_ts >= ? AND start_ts < ?
            GROUP BY process
            """,
            (today, tomorrow),
        ).fetchall()
        return {r["process"]: float(r["total_seconds"] or 0.0) for r in rows}


def query_recent_sessions(limit: int = 30) -> list[dict]:
    """最近 N 条原始会话."""
    sec = _seconds_sql("start_ts", "end_ts")
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT start_ts, end_ts, process, window_title, {sec} AS seconds
            FROM sessions
            ORDER BY start_ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def query_total_seconds() -> float:
    """全库总秒数."""
    sec = _seconds_sql("start_ts", "end_ts")
    with _connect() as conn:
        row = conn.execute(f"SELECT COALESCE(SUM({sec}), 0) AS total FROM sessions").fetchone()
        return float(row["total"] or 0)


# ---------- 给前端用的便捷入口 ----------

def dashboard_today() -> dict:
    today, _ = _today_range()
    return {
        "range": {"start": today, "kind": "today"},
        "top_apps": query_top_apps(*_today_range()),
        "hourly": query_hourly_today(),
        "recent": query_recent_sessions(20),
    }


def dashboard_week() -> dict:
    start, end = _week_range()
    return {
        "range": {"start": start, "end": end, "kind": "week"},
        "top_apps": query_top_apps(start, end),
        "daily": query_daily_total(start, end),
    }


def dashboard_month() -> dict:
    start, end = _month_range()
    return {
        "range": {"start": start, "end": end, "kind": "month"},
        "top_apps": query_top_apps(start, end),
        "daily": query_daily_total(start, end),
    }


def dashboard_overview() -> dict:
    """总览：今日/本周/本月 + 全库总时长."""
    today_s, _ = _today_range()
    week_s, week_e = _week_range()
    month_s, month_e = _month_range()
    return {
        "today_seconds": sum(a["total_seconds"] for a in query_top_apps(*_today_range())),
        "week_seconds": sum(a["total_seconds"] for a in query_top_apps(week_s, week_e)),
        "month_seconds": sum(a["total_seconds"] for a in query_top_apps(month_s, month_e)),
        "total_seconds": query_total_seconds(),
        "today_top": query_top_apps(*_today_range(), limit=5),
    }


# ---------- 管理页面用的数据 (布局参考手机「屏幕使用时长」) ----------

def _apps_with_share(rows: list[dict], limit: int = 50) -> list[dict]:
    """给应用列表补上占比, 前端画长度条用."""
    total = sum(float(r.get("total_seconds") or 0.0) for r in rows) or 1.0
    out = []
    for r in rows[:limit]:
        s = float(r.get("total_seconds") or 0.0)
        out.append({
            "name": r.get("process") or "(unknown)",
            "seconds": round(s, 1),
            "share": round(s / total, 4),
            "exe": r.get("exe_path"),
            "sessions": r.get("sessions"),
        })
    return out


def page_day() -> dict:
    """「每天」页: 大字总时长 + 比昨天 + 24 小时柱状 + 应用列表."""
    today_s, today_e = _day_range(0)
    y_s, y_e = _day_range(1)
    bars = query_hourly_between(today_s, today_e)
    return {
        "kind": "day",
        "label": "今天",
        "total_seconds": round(sum(bars), 1),
        "prev_seconds": round(query_range_total(y_s, y_e), 1),
        "prev_label": "昨天",
        "bars": [{"key": str(h), "seconds": round(v, 1)} for h, v in enumerate(bars)],
        # 只给要显示的刻度, 其余留空 (前端画 x 轴文字用)
        "axis_labels": {"0": "0:00", "6": "6:00", "12": "12:00", "18": "18:00", "23": "24:00"},
        "apps": _apps_with_share(query_top_apps_between(today_s, today_e)),
        "range": {"start": today_s, "end": today_e},
    }


def page_week() -> dict:
    """「每周」页: 本周总时长 + 比上周 + 7 天柱状 + 应用列表."""
    wk_s, wk_e = _week_range()
    lw_s, lw_e = _last_week_range()

    by_day = {d["day"]: float(d["total_seconds"] or 0.0)
              for d in query_daily_total(wk_s, wk_e)}
    # 从本周一补到今天 (缺的补 0), 保证柱子数量稳定
    start = datetime.strptime(wk_s, "%Y-%m-%d")
    today = datetime.now()
    bars = []
    cursor = start
    while cursor.date() <= today.date():
        key = cursor.strftime("%Y-%m-%d")
        bars.append({"key": key, "seconds": round(by_day.get(key, 0.0), 1)})
        cursor += timedelta(days=1)

    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    axis = {}
    for i, b in enumerate(bars):
        d = datetime.strptime(b["key"], "%Y-%m-%d")
        axis[str(i)] = weekdays[d.weekday()]

    return {
        "kind": "week",
        "label": "本周",
        "total_seconds": round(sum(by_day.values()), 1),
        "prev_seconds": round(query_range_total(lw_s, lw_e), 1),
        "prev_label": "上周",
        "bars": bars,
        "axis_labels": axis,
        "apps": _apps_with_share(query_top_apps_between(wk_s, wk_e)),
        "range": {"start": wk_s, "end": wk_e},
    }


if __name__ == "__main__":
    init_db()
    print("db initialized at:", DB_PATH)
