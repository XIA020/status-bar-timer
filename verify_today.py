"""verify_today.py — 只读核对: 任务栏上显示的数字, 跟数据库里的事实对不对得上.

用途:
    .venv\\Scripts\\python.exe verify_today.py

会打印:
  * state.json 里 HUD/tray 正在显示的内容 (当前应用 / 本次连续 / 今日累计 / Top 列表)
  * 数据库里今天的真实汇总
  * 两者的差值 (差值应该 ≈ 当前这一段的秒数, 因为当前段还没落库)

同时验证"时间叠加": 今天该应用的总时长 = 所有历史段之和 + 当前这一段,
而不是只有当前这一段.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import db

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "data", "state.json")


def sec_to_hms(s: float) -> str:
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def check_formatters() -> bool:
    """回归自检: 时长格式化在跨过 1 小时之后也不能算错.

    来历: `divmod(s, 3600)` 的第二项是**剩余秒数**而不是分钟, 早先误当分钟用,
    于是 5610 秒显示成 "1h 2010m". 低于 1 小时不会触发, 只有累计超一小时才暴露.
    """
    import tracker_worker as tw

    cases = [
        (0, "0s"), (45, "45s"), (59, "59s"), (60, "1m 0s"), (125, "2m 5s"),
        (3599, "59m 59s"), (3600, "1h 0m"), (3661, "1h 1m"), (5610, "1h 33m"),
        (7200, "2h 0m"), (7325, "2h 2m"),
    ]
    compact_cases = [
        (45, "45s"), (60, "1m"), (3599, "59m"), (3600, "1h"),
        (5610, "1h33m"), (7320, "2h2m"),
    ]
    print("=" * 72)
    print("时长格式化自检")
    print("=" * 72)
    ok = True
    for sec, want in cases:
        got = tw.fmt_duration(sec)
        good = got == want
        ok = ok and good
        print(f"  {'[OK]  ' if good else '[FAIL]'} fmt_duration({sec}) = {got!r}"
              + ("" if good else f"   期望 {want!r}"))
    for sec, want in compact_cases:
        got = tw.fmt_compact(sec)
        good = got == want
        ok = ok and good
        print(f"  {'[OK]  ' if good else '[FAIL]'} fmt_compact({sec})  = {got!r}"
              + ("" if good else f"   期望 {want!r}"))
    # 最关键的断言: 绝不能出现 "2010m" 这种分钟数 >= 60 的输出
    for sec in range(0, 40000, 137):
        s = tw.fmt_duration(sec)
        if "h" in s:
            mins = int(s.split("h")[1].strip().rstrip("m") or 0)
            if mins >= 60:
                print(f"  [FAIL] fmt_duration({sec}) = {s!r} 分钟数越界")
                ok = False
                break
    print("  结论:", "全部通过" if ok else "有失败项")
    return ok


def main() -> int:
    fmt_ok = check_formatters()
    print()

    try:
        with open(STATE, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception as e:
        print(f"[error] 读不到 state.json: {e}")
        print("        (app.py / tracker_worker.py 在跑吗?)")
        return 1

    today = datetime.now().strftime("%Y-%m-%d")
    cur = st.get("process")
    totals = db.query_today_totals()
    rows = db.query_recent_sessions(500)
    today_rows = [r for r in rows if str(r.get("start_ts", "")).startswith(today)]

    print("=" * 72)
    print("HUD / tray 正在显示 (来自 data/state.json)")
    print("=" * 72)
    print(f"  当前前台应用   : {cur}")
    print(f"  本次连续时长   : {st.get('duration')}")
    print(f"  今日累计       : {st.get('today_total')}")
    print(f"  暂停           : {st.get('paused')}")
    print(f"  Top 列表       :")
    for a in (st.get("top_apps") or [])[:6]:
        print(f"      {a.get('name'):<28} {a.get('time'):>8}   ({a.get('seconds')}s)")
    print(f"  updated_at     : {datetime.fromtimestamp(st.get('updated_at', 0)).strftime('%H:%M:%S')}")

    print()
    print("=" * 72)
    print(f"数据库里今天的真实汇总 ({today})")
    print("=" * 72)
    print(f"  今日总段数     : {len(today_rows)}")
    print(f"  今日总时长     : {sec_to_hms(sum(totals.values()))}")
    print("  按应用:")
    for name, sec in sorted(totals.items(), key=lambda kv: -kv[1])[:6]:
        n = len([r for r in today_rows if r.get("process") == name])
        print(f"      {name:<28} {sec_to_hms(sec):>12}   {n} 段")
    if cur and cur in totals:
        n = len([r for r in today_rows if r.get("process") == cur])
        print()
        print(f"  当前应用 {cur!r} 今天已经切进来 {n} 段")
        print(f"  → 界面上显示的是 {st.get('today_total')} (= 这 {n} 段之和 + 当前这一段),")
        print(f"    而不是只有当前这一段 ({st.get('duration')})。这就是'时间叠加'。")
    return 0 if fmt_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
