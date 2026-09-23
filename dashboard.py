"""Dashboard 窗口 (独立子进程).

架构:
    1. bottle HTTP server on 127.0.0.1:<port> 提供 /api/* JSON endpoints
    2. pywebview 加载 dashboard.html, HTML 里 fetch 同机 HTTP server 的 API
    3. 关窗口时清理 HTTP server 线程

为什么不用 pywebview js_api: pywebview 6.2.1 的 winforms backend 下 js_callback
从未注册, promise 永远不 resolve. 所以自己起 HTTP server 更靠谱.
"""
from __future__ import annotations

import os
import sys
import json
import threading
import webview

import db
import paths

HTML_PATH = os.path.join(paths.res_dir(), "dashboard.html")
HTTP_PORT = None  # set after server starts


# ==================== API 桥接 ====================

def _make_api_app():
    """返回 bottle app:
        - /api/* JSON endpoints
        - / 和 /static/* 静态文件 (HTML/CSS/JS/ECharts)
    """
    import bottle

    app = bottle.Bottle()
    base_dir = paths.res_dir()

    def _json(data):
        bottle.response.headers['Content-Type'] = 'application/json'
        bottle.response.headers['Access-Control-Allow-Origin'] = '*'
        return json.dumps(data, default=str)

    @app.get('/api/overview')
    def overview():
        return _json(db.dashboard_overview())

    # ---- 管理页面 (布局参考手机「屏幕使用时长」) ----
    @app.get('/api/page/<kind>')
    def page(kind):
        if kind == 'day':
            return _json(db.page_day())
        if kind == 'week':
            return _json(db.page_week())
        bottle.HTTPError(404, f"unknown page: {kind}")

    @app.get('/api/icon')
    def icon():
        """按 exe 路径返回应用图标 PNG. 抽不到就 404, 前端退化成首字母头像.

        ⚠️ exe 路径用 **base64url** 传, 不直接放进查询串.
        查询串里带中文时 bottle 那层解码出来的路径会变形 (实测 "D:\\抖音\\..." 拿到的是
        另一个字符串, 结果抽不到图标), base64url 全 ASCII 就没这问题.
        顺带也不把用户的真实文件路径暴露在 URL 里.
        """
        import base64
        import app_icons

        def _b64url(s: str) -> str:
            s = s.replace('-', '+').replace('_', '/')
            s += '=' * (-len(s) % 4)
            return base64.b64decode(s).decode('utf-8', 'replace')

        raw = (bottle.request.query.get('b64') or '').strip()
        exe = _b64url(raw) if raw else (bottle.request.query.get('exe') or '').strip()
        if not exe or not exe.lower().endswith('.exe'):
            bottle.HTTPError(404, 'bad exe')
        data = app_icons.extract_icon_png(exe)
        if not data:
            bottle.HTTPError(404, 'no icon')
        bottle.response.headers['Content-Type'] = 'image/png'
        bottle.response.headers['Cache-Control'] = 'max-age=86400'
        return data

    @app.get('/api/today')
    def today():
        return _json(db.dashboard_today())

    @app.get('/api/week')
    def week():
        return _json(db.dashboard_week())

    @app.get('/api/month')
    def month():
        return _json(db.dashboard_month())

    @app.get('/api/status')
    def status():
        try:
            total = db.query_total_seconds()
            return _json({"has_data": total > 0, "total_seconds": total})
        except Exception as e:
            return _json({"has_data": False, "error": str(e)})

    @app.get('/')
    def root():
        # 重定向到 dashboard.html
        bottle.redirect('/static/dashboard.html')

    @app.get('/static/<filename:path>')
    def static_file(filename):
        # 静态文件 (相对 base_dir)
        path = os.path.join(base_dir, filename)
        if not os.path.exists(path):
            bottle.HTTPError(404, f"{filename} not found")
        bottle.response.headers['Cache-Control'] = 'no-cache'
        return bottle.static_file(filename, root=base_dir)

    return app


def _make_server(app, port: int):
    """多线程 WSGI server.

    页面会并发请求 (JSON + 每个应用一个图标), bottle 默认的 wsgiref 是单线程,
    串行处理会明显拖慢首屏, 所以换成 ThreadingMixIn 版本.
    """
    from wsgiref.simple_server import make_server, WSGIServer
    from socketserver import ThreadingMixIn

    class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True
        allow_reuse_address = True

    return make_server('127.0.0.1', port, app, ThreadedWSGIServer)


def _free_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_http_server():
    """在后台线程启动 HTTP server. 返回 (host, port)."""
    import time
    import urllib.request

    port = _free_port()
    srv = _make_server(_make_api_app(), port)
    threading.Thread(target=srv.serve_forever, daemon=True,
                     name='DashboardHTTPServer').start()
    # 等服务器 ready
    for _ in range(50):
        time.sleep(0.05)
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status', timeout=1).read()
            break
        except Exception:
            continue
    return ('127.0.0.1', port)


# ==================== 截图工具 ====================

def _screenshot_window(window, out_path: str):
    import ctypes
    from ctypes import wintypes
    from PIL import Image

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    hwnd = None
    try:
        hwnd = int(window._window.Handle)  # type: ignore[attr-defined]
    except Exception:
        hwnd = user32.FindWindowW(None, "Screen Time Tracker")
    if not hwnd:
        print("[screenshot] cannot find hwnd", flush=True)
        return

    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    import time as _t
    _t.sleep(0.5)

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top

    hdc_window = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    hbm = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
    gdi32.SelectObject(hdc_mem, hbm)
    user32.PrintWindow(hwnd, hdc_mem, 0x00000002)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]
    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0

    buf_len = w * h * 4
    buf = (ctypes.c_ubyte * buf_len)()
    gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bmi), 0)

    img = Image.frombuffer("RGBA", (w, h), bytes(buf), "raw", "BGRA", 0, 1)
    img = img.convert("RGB")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG")
    print(f"[screenshot] saved {out_path} ({w}x{h})", flush=True)

    gdi32.DeleteObject(hbm)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)


# ==================== 入口 ====================

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-close", type=int, default=0, help="Auto-close after N seconds")
    parser.add_argument("--screenshot", type=str, default=None, help="Save screenshot and exit")
    parser.add_argument("--serve-only", action="store_true",
                        help="只起 HTTP server 不开窗口 (用浏览器调试页面用)")
    parser.add_argument("--port", type=int, default=0, help="配合 --serve-only 固定端口")
    args = parser.parse_args()

    if not os.path.exists(HTML_PATH):
        print(f"[dashboard] {HTML_PATH} not found", file=sys.stderr)
        sys.exit(1)

    db.init_db()

    if args.serve_only:
        port = args.port or _free_port()
        print(f"[dashboard] serve-only: http://127.0.0.1:{port}/", flush=True)
        try:
            _make_server(_make_api_app(), port).serve_forever()
        except KeyboardInterrupt:
            pass
        return

    # 启动自己的 HTTP server
    host, port = _start_http_server()
    global HTTP_PORT
    HTTP_PORT = port
    print(f"[dashboard] HTTP API server: http://{host}:{port}", flush=True)

    # 启动 pywebview 窗口 (通过 HTTP server 加载, 不是 file://, 避免 mixed-content 阻止 fetch)
    window = webview.create_window(
        title="状态栏记时器",
        url=f"http://{host}:{port}/",
        width=520,
        height=860,
        min_size=(420, 620),
        background_color="#111318",
        text_select=True,
    )

    # 不需要再注入 __API_BASE__: HTML 和 API 同源 (都是 http://127.0.0.1:port)

    if args.auto_close > 0 or args.screenshot:
        def _killer():
            import time
            wait = max(args.auto_close, 10) if args.screenshot else args.auto_close
            time.sleep(wait)
            if args.screenshot:
                try:
                    _screenshot_window(window, args.screenshot)
                except Exception as e:
                    print(f"[dashboard] screenshot failed: {e}", flush=True)
            try:
                window.destroy()
            except Exception:
                pass
        threading.Thread(target=_killer, daemon=True).start()

    webview.start(debug=False)


if __name__ == "__main__":
    main()
