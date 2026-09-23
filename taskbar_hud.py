r"""taskbar_hud.py — 把前台应用用时真正嵌进 Windows 任务栏.

路线 A: 独立进程 + SetParent, 不注入 explorer.exe.

    1. FindWindow("Shell_TrayWnd") 拿到任务栏
    2. 自己的窗口去掉 WS_POPUP 加 WS_CHILD, SetParent 到任务栏
    3. 从任务栏各子窗口的屏幕矩形算出空闲区间, 把窗口定位进去
       (自动避开开始按钮 / 任务按钮区 / 托盘区)
    4. WS_EX_LAYERED + UpdateLayeredWindow 逐像素 alpha
       → 屏幕上只有文字, 没有方块背景和边框
    5. 监听 TaskbarCreated 消息: explorer 崩溃重启后自动重新停靠

数据来源: data/state.json   (tracker_worker.py 每秒写)
配置文件: data/hud_config.json (不存在则用内置默认值, 自动生成)
拖动位置: data/hud_pos.json  (拖过之后写入手动 x)
日志:     hud.log

交互:
    左键拖动   → 调整在任务栏里的水平位置 (松手后记住)
    双击/右键  → 打开 dashboard
    跟随任务栏 → 任务栏自动隐藏/切分辨率/explorer 重启都能跟上

单独调试:
    .venv\Scripts\python.exe taskbar_hud.py                    # 正常跑
    .venv\Scripts\python.exe taskbar_hud.py --selftest         # 自检: 停靠+渲染一次, 打印结果后退出
    .venv\Scripts\python.exe taskbar_hud.py --check            # 只读: 看运行中的 HUD 挂在哪、显示了什么
    .venv\Scripts\python.exe taskbar_hud.py --test-recovery    # 测 explorer 重建任务栏后的自动重停靠
    .venv\Scripts\python.exe taskbar_hud.py --test-recovery-hard  # 再测健康检查自愈(会有几秒浮窗)
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import traceback
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

import paths

# ==================== 路径 ====================
ROOT = paths.app_dir()
DATA_DIR = os.path.join(ROOT, "data")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
CONTROL_PATH = os.path.join(DATA_DIR, "control.json")
CONFIG_PATH = os.path.join(DATA_DIR, "hud_config.json")
POS_PATH = os.path.join(DATA_DIR, "hud_pos.json")
STATUS_PATH = os.path.join(DATA_DIR, "hud_status.json")
LOG_PATH = os.path.join(ROOT, "hud.log")

# ==================== Win32 常量 ====================
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000

WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008

GWL_STYLE = -16
GWL_EXSTYLE = -20

CS_VREDRAW = 0x0001
CS_HREDRAW = 0x0002
CS_DBLCLKS = 0x0008

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080

GW_CHILD = 5
GW_HWNDNEXT = 2

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_MOUSEACTIVATE = 0x0021
WM_SETCURSOR = 0x0020
WM_TIMER = 0x0113
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_MOUSEMOVE = 0x0200
WM_DISPLAYCHANGE = 0x007E
WM_SETTINGCHANGE = 0x001A
WM_DPICHANGED = 0x02E0
WM_NCDESTROY = 0x0082

MA_NOACTIVATE = 3

DT_LEFT = 0x0000
DT_TOP = 0x0000
DT_CENTER = 0x0001
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0020
DT_NOPREFIX = 0x0800

TRANSPARENT = 1
ANTIALIASED_QUALITY = 4
FW_NORMAL = 400
FW_BOLD = 700
DEFAULT_CHARSET = 1
OUT_TT_PRECIS = 5
CLIP_DEFAULT_PRECIS = 0
DEFAULT_PITCH = 0
FF_DONTCARE = 0
DIB_RGB_COLORS = 0
ULW_ALPHA = 2
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1

IDC_SIZEALL = 32646
TIMER_ID = 1
HUD_CLASS = "ScreenTimeTrackerTaskbarHud"
TASKBAR_CREATED = user32.RegisterWindowMessageW("TaskbarCreated")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
ENUMWINDOWSPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


# ==================== 结构体 ====================
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


# ==================== API 声明 ====================
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.SetParent.restype = wintypes.HWND
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.EnumChildWindows.argtypes = [wintypes.HWND, ENUMWINDOWSPROC, wintypes.LPARAM]
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
user32.SetCapture.restype = wintypes.HWND
user32.SetCapture.argtypes = [wintypes.HWND]
user32.ReleaseCapture.argtypes = []
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT),
                                       ctypes.POINTER(SIZE), wintypes.HDC, ctypes.POINTER(POINT),
                                       wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
user32.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
user32.SetProcessDPIAware.restype = wintypes.BOOL
user32.SetProcessDPIAware.argtypes = []

gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.CreateFontW.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                              wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                              wintypes.LPCWSTR]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                        ctypes.POINTER(SIZE)]
# DrawTextW 属于 user32, 不是 gdi32
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(wintypes.RECT), wintypes.UINT]

kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

ERROR_ALREADY_EXISTS = 183


# ==================== DPI ====================
def set_dpi_awareness() -> str:
    """必须在建任何窗口之前调用.

    不声明 DPI 感知的话, 进程看到的是 96 DPI 的虚拟坐标, 而 explorer/任务栏 跑在真实
    DPI (本机 144=150%). 把一个"不感知"的窗口 SetParent 给"感知"的任务栏, Windows 会
    给子窗口套一层缩放 —— 实测窗口尺寸会被乘 96/144=2/3, 位置也全错.
    声明成 per-monitor-v2 之后, 双方坐标系统一为物理像素.
    """
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
    try:
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        if shcore.SetProcessDpiAwareness(2) == 0:   # PROCESS_PER_MONITOR_DPI_AWARE
            return "per-monitor"
    except Exception:
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "none"


# ==================== 日志 ====================
def log(msg: str) -> None:
    line = f"[hud] {time_str()} {msg}"
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512 * 1024:
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.write("[hud] log rotated\n")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def time_str() -> str:
    import datetime
    return datetime.datetime.now().strftime("%H:%M:%S")


def _h(v) -> str:
    """ctypes 的 HWND 是 c_void_p, NULL 会变成 None, 格式化时要用这个."""
    if v is None:
        return "None(NULL)"
    try:
        return f"{int(v):#x}"
    except Exception:
        return repr(v)


# ==================== 配置 ====================
DEFAULT_CONFIG = {
    "enabled": True,
    # 位置: right = 贴在托盘区左边; left = 贴任务栏最左; center = 最长空闲段居中; manual = 用 hud_pos.json 的 x
    "anchor": "right",
    "gap_px": 10,              # 与相邻任务栏元素的间距
    "min_width_px": 60,
    "max_width_px": 460,
    "font_name": "Segoe UI",
    "font_size_px": 12,        # 12 ≈ Win11 任务栏时钟的字号, 也更容易在一行里塞下 Top 列表
    "font_bold": False,
    "text_color": "auto",      # auto = 跟随系统浅色/深色主题; 或 "#RRGGBB"
    "background": False,       # True = 文字后面加一个半透明圆角底
    "bg_color": "#1C1C1C",
    "bg_alpha": 90,
    "corner_radius_px": 6,
    "padding_x_px": 10,        # 文字左右留白
    "text_offset_y_px": 0,     # 文字垂直微调 (负=上移)
    "format_active": "{process} ({duration})",
    "format_paused": "已暂停",
    "format_idle": "空闲",
    "hide_when_paused": False,
    # 主计时显示哪个数: today_total = 今日累计 (切走再切回来会接着涨) / current = 本次连续
    "duration_mode": "today_total",
    # 后面跟一段今日用最多的几个应用
    "show_top_apps": True,
    "top_apps_count": 3,
    "top_apps_gap": "   ",              # 计时与列表之间的间隔
    "top_apps_item_sep": " · ",         # 列表项之间的分隔
    "top_apps_time_format": "compact",  # compact = 12m / 2h3m; long = 12m 30s / 2h 3m
    "top_apps_include_current": False,  # 列表里要不要再列一次当前应用 (主计时已经显示了)
    "top_apps_name_max": 16,            # 应用名超过这个长度就截断 (0 = 不截断)
    "dim_alpha": 150,                   # 列表那段的不透明度 (0-255), 越低越"退后"
    "poll_ms": 500,            # 读 state.json 的间隔
    "health_every": 10,        # 每 N 次轮询做一次停靠健康检查
    "drag_threshold_px": 4,    # 光标移动超过这个距离才算拖动 (防止轻点一下就把位置钉死)
}

_config_cache: dict | None = None


_last_good_config: dict | None = None


def load_config() -> dict:
    """读配置.

    ⚠️ 解析失败时**不能**回退成 DEFAULT_CONFIG —— 用户正在编辑器里改这个文件时,
    读到半个 JSON 就会瞬间回退成默认, 表现是计时器突然跳到别的位置再跳回来.
    所以解析失败时沿用上一次读到的好配置.
    """
    global _last_good_config
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update(user)
            _last_good_config = cfg
            return cfg
    except FileNotFoundError:
        # 首次运行 (或用户删了想重置): 落一份默认配置, 方便改
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        _last_good_config = cfg
        return cfg
    except Exception:
        pass
    if _last_good_config is not None:
        return _last_good_config
    return cfg


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as e:
        log(f"save_config err: {e}")


def load_pos() -> dict:
    try:
        with open(POS_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_pos(d: dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = POS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, POS_PATH)
    except Exception as e:
        log(f"save_pos err: {e}")


_last_good_state: dict = {}


def read_state() -> dict:
    """读 state.json.

    读取的一瞬间可能正好撞上 tracker 在替换文件 (写 tmp + rename), 这时解析会失败.
    解析失败就用上一次的好数据, 免得界面闪一下"空闲".
    """
    global _last_good_state
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict):
            _last_good_state = st
            return st
    except Exception:
        pass
    return _last_good_state


def control_shutdown_requested() -> bool:
    try:
        with open(CONTROL_PATH, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("shutdown"))
    except Exception:
        return False


def system_uses_light_theme() -> bool:
    """读系统主题: 任务栏是浅色还是深色."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "SystemUsesLightTheme")[0])
    except Exception:
        return False


def parse_color(s: str):
    s = (s or "").lstrip("#")
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


# ==================== HUD 主体 ====================
class TaskbarHud:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.hwnd = 0
        self.taskbar = 0
        self._docked = False
        self._w = 0
        self._h = 0
        self._text = ""
        self._seg_key = None
        self._visible = False
        self._ticks = 0
        self._last_state = {}
        self._manual_x = load_pos().get("x")
        self._drag = None          # dict: origin_screen_x / origin_win_x
        self._wndproc_ref = None
        self._memdc = 0
        self._dib = 0
        self._bits = None
        self._buf = None
        self._old_bmp = 0
        self._font = 0
        self._old_font = 0
        self._measure_dc = 0
        self._screen_dc = 0
        self._bg_cache = None      # (w, h, c_uint32 array)
        self._font_key = None
        self._text_color_rgb = (230, 230, 230)
        self._selftest = False

    # ---------- DPI ----------
    def _dpi_scale(self) -> float:
        """配置里的数值一律按 96 DPI 的逻辑像素写, 这里换算成物理像素."""
        if not (self.taskbar or self.hwnd):
            return 1.0
        dpi = user32.GetDpiForWindow(self.taskbar or self.hwnd) or 96
        return dpi / 96.0

    def _px(self, key: str, default: int) -> int:
        try:
            v = int(self.cfg.get(key, default))
        except Exception:
            v = default
        return max(0, int(round(v * self._dpi_scale())))

    # ---------- 窗口 ----------
    def create_window(self) -> bool:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        # CS_DBLCLKS: 没有它双击只会来两次 WM_LBUTTONDOWN
        wc.style = CS_HREDRAW | CS_VREDRAW | CS_DBLCLKS
        self._wndproc_ref = WNDPROC(self._wndproc)
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.cast(ctypes.c_void_p(IDC_SIZEALL),
                                                          wintypes.LPCWSTR))
        wc.lpszClassName = HUD_CLASS
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                log(f"RegisterClassExW failed err={err}")
                return False

        self._screen_dc = user32.GetDC(None)
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST,
            HUD_CLASS, "Screen Time Tracker", WS_POPUP,
            -4000, -4000, 10, 10, None, None, hinst, None)
        if not self.hwnd:
            log(f"CreateWindowExW failed err={ctypes.get_last_error()}")
            return False
        log(f"window created hwnd={self.hwnd:#x}")
        return True

    def _find_taskbar(self):
        hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if hwnd and user32.IsWindow(hwnd):
            return hwnd
        return 0

    def dock(self) -> bool:
        """把窗口变成任务栏的子窗口."""
        taskbar = self._find_taskbar()
        if not taskbar:
            self._docked = False
            return False
        if self._docked and user32.GetParent(self.hwnd) == taskbar:
            self.taskbar = taskbar
            return True

        # 若之前停靠过, 先干净解挂, 避免反复切父窗口时样式残留
        if self._docked:
            self.undock(keep_hidden=True)

        prev_style = user32.GetWindowLongW(self.hwnd, GWL_STYLE)
        new_style = (prev_style & ~WS_POPUP) | WS_CHILD
        ctypes.set_last_error(0)
        user32.SetWindowLongW(self.hwnd, GWL_STYLE, new_style)
        err_swl = ctypes.get_last_error()
        ctypes.set_last_error(0)
        old_parent = user32.SetParent(self.hwnd, taskbar)
        err_sp = ctypes.get_last_error()
        parent_now = user32.GetParent(self.hwnd)
        user32.SetWindowPos(self.hwnd, None, 0, 0, 10, 10,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        self.taskbar = taskbar
        self._docked = parent_now == taskbar
        log(f"dock -> {self._docked}  taskbar={taskbar:#x} "
            f"old_parent={_h(old_parent)} parent_now={_h(parent_now)} "
            f"err_swl={err_swl} err_sp={err_sp} "
            f"style {prev_style:#010x} -> {new_style:#010x} "
            f"child_bit={bool(user32.GetWindowLongW(self.hwnd, GWL_STYLE) & WS_CHILD)}")
        return self._docked

    def undock(self, keep_hidden: bool = False) -> None:
        """解挂: 先隐藏 -> SetParent(null) -> 样式还原."""
        if not self.hwnd or not self._docked:
            return
        user32.SetWindowPos(self.hwnd, None, -4000, -4000, 10, 10,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_HIDEWINDOW)
        user32.SetParent(self.hwnd, None)
        style = user32.GetWindowLongW(self.hwnd, GWL_STYLE)
        user32.SetWindowLongW(self.hwnd, GWL_STYLE, (style & ~WS_CHILD) | WS_POPUP)
        user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        self._docked = False
        self._visible = False

    def _show(self, visible: bool) -> None:
        if not self.hwnd:
            return
        flag = SWP_SHOWWINDOW if visible else SWP_HIDEWINDOW
        user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | flag)
        self._visible = visible

    # ---------- 任务栏几何 ----------
    def _occupied_ranges(self):
        """收集任务栏上真正占位的东西 (屏幕坐标).

        递归走下 3 层: Win11 上任务按钮区有时是 ReBarWindow32 的直接子窗口,
        有时藏在合成层里面, 只扫一层会漏.

        过滤规则 (关键):
          * 跳过自己
          * 跳过不可见的
          * 跳过 0 宽度和"几乎整条任务栏宽"的窗口 —— 后者是
            Windows.UI.Composition.DesktopWindowContentBridge 这类背景合成层,
            把它们当占用会得出"整条任务栏都是满的".
        """
        r = wintypes.RECT()
        if not user32.GetWindowRect(self.taskbar, ctypes.byref(r)):
            return []
        tb_left, tb_right = r.left, r.right
        tb_width = tb_right - tb_left
        if tb_width <= 0:
            return []
        limit = tb_width * 0.9
        occupied = []

        def walk(parent, depth):
            if depth > 3:
                return
            child = user32.GetWindow(parent, GW_CHILD)
            while child:
                if child != self.hwnd and user32.IsWindowVisible(child):
                    cr = wintypes.RECT()
                    if user32.GetWindowRect(child, ctypes.byref(cr)):
                        w = cr.right - cr.left
                        if 0 < w < limit:
                            occupied.append((cr.left, cr.right))
                    walk(child, depth + 1)
                child = user32.GetWindow(child, GW_HWNDNEXT)

        walk(self.taskbar, 1)
        return occupied

    def _free_ranges(self):
        """算出任务栏上没被占用的水平区间 (屏幕坐标), 已按 gap 收缩."""
        r = wintypes.RECT()
        if not user32.GetWindowRect(self.taskbar, ctypes.byref(r)):
            return []
        tb_left, tb_right = r.left, r.right
        if tb_right - tb_left <= 0:
            return []
        gap = self._px("gap_px", 10)

        occupied = [(s - gap, e + gap) for s, e in self._occupied_ranges()]

        # 合并重叠区间 (按起点排序后线性扫)
        merged = []
        for s, e in sorted(occupied):
            s = max(s, tb_left)
            e = min(e, tb_right)
            if e <= s:
                continue
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])

        # 取补集
        free = []
        cursor = tb_left
        for s, e in merged:
            if s > cursor:
                free.append((cursor, s))
            cursor = max(cursor, e)
        if cursor < tb_right:
            free.append((cursor, tb_right))
        return [(s, e) for s, e in free if e - s > 0]

    def _choose_range(self, ranges):
        """先挑目标空闲段, 再按这段的宽度裁剪内容.

        ⚠️ 顺序很重要. 早先是"先按最长空闲段算宽度, 再找放得下这段宽度的区间",
        结果右边空档比内容窄 1px 就整体跳到任务栏左半去 —— 内容一宽就"乱跳".
        现在改成: 位置只由 anchor 决定 (默认永远贴托盘左边), 空间不够就少显示几项.
        """
        if not ranges:
            return None
        anchor = str(self.cfg.get("anchor", "right")).lower()
        min_w = self._px("min_width_px", 60)

        if anchor == "manual" and self._manual_x is not None:
            mx = int(self._manual_x)
            for r in ranges:
                if r[0] - min_w <= mx <= r[1]:
                    return r
            return max(ranges, key=lambda r: r[1] - r[0])

        if anchor == "left":
            cand = ranges[0]
        elif anchor == "center":
            cand = max(ranges, key=lambda r: r[1] - r[0])
        else:                       # right / 其它一律当 right
            cand = ranges[-1]

        # 目标段窄到连最小宽度都放不下时, 退到最长的那段
        if (cand[1] - cand[0]) < min_w:
            cand = max(ranges, key=lambda r: r[1] - r[0])
        return cand

    def _anchor_x(self, rng, width: int):
        """在选定的空闲段里, 按 anchor 偏好算窗口左边界的屏幕 x."""
        anchor = str(self.cfg.get("anchor", "right")).lower()
        s, e = rng
        if anchor == "manual" and self._manual_x is not None:
            return int(self._manual_x)
        if anchor == "left":
            return s
        if anchor == "center":
            return s + (e - s - width) // 2
        return e - width

    # ---------- 文本 ----------
    def _ensure_font(self) -> None:
        """按 DPI/配置建字体 (配置或 DPI 变了才重建)."""
        dpi = user32.GetDpiForWindow(self.taskbar or self.hwnd) or 96
        scale = dpi / 96.0
        size = max(8, int(round(int(self.cfg.get("font_size_px", 13)) * scale)))
        bold = bool(self.cfg.get("font_bold", False))
        name = str(self.cfg.get("font_name", "Segoe UI"))
        key = (size, bold, name)
        if key == self._font_key and self._font:
            return
        if not self._measure_dc:
            self._measure_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        new_font = gdi32.CreateFontW(
            -size, 0, 0, 0, FW_BOLD if bold else FW_NORMAL, 0, 0, 0,
            DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
            ANTIALIASED_QUALITY, DEFAULT_PITCH | FF_DONTCARE, name)
        if not new_font:
            return
        # 先把新字体选进两个 DC, 再删旧字体 —— 否则删的是仍被 DC 引用的对象
        old_measure = gdi32.SelectObject(self._measure_dc, new_font)
        if self._memdc:
            gdi32.SelectObject(self._memdc, new_font)
        old_font = self._font
        self._font = new_font
        if old_font:
            gdi32.DeleteObject(old_font)
        self._old_font = old_measure
        self._font_key = key
        log(f"font {key} dpi={dpi}")

    def _measure(self, text: str) -> int:
        sz = SIZE()
        if not gdi32.GetTextExtentPoint32W(self._measure_dc, text, len(text), ctypes.byref(sz)):
            return len(text) * 8
        return sz.cx

    def _fit_text(self, text: str, max_px: int) -> str:
        """超宽就截断, 尽量保住时长信息."""
        if self._measure(text) <= max_px:
            return text
        # 逐字截断加省略号
        for keep in range(len(text) - 1, 0, -1):
            cand = text[:keep] + "…"
            if self._measure(cand) <= max_px:
                return cand
        return "…"

    def build_text(self, st: dict) -> str:
        """主计时那一段的文字."""
        mode = str(self.cfg.get("duration_mode", "today_total")).lower()
        if mode == "current":
            dur = st.get("duration") or "0s"
        else:
            dur = st.get("today_total") or st.get("duration") or "0s"
        proc = st.get("process")
        if st.get("paused"):
            return str(self.cfg.get("format_paused", "已暂停")).format(process="", duration=dur)
        if not proc:
            return str(self.cfg.get("format_idle", "空闲")).format(process="", duration=dur)
        fmt = str(self.cfg.get("format_active", "{process} ({duration})"))
        try:
            return fmt.format(process=proc, duration=dur)
        except Exception:
            return f"{proc} ({dur})"

    def _short_name(self, name: str) -> str:
        """列表里用的短名: 去掉 .exe 尾巴, 过长截断."""
        n = str(name or "?").strip()
        if n.lower().endswith(".exe"):
            n = n[:-4]
        try:
            mx = int(self.cfg.get("top_apps_name_max", 14))
        except Exception:
            mx = 14
        if mx > 0 and len(n) > mx:
            n = n[:mx - 1] + "…"
        return n or "?"

    def _build_segments(self, st: dict, limit_px: int):
        """拼出「主计时 + 今日 Top 列表」两段, 返回 (segments, line_width).

        segments = [(text, (r,g,b), alpha_mul)], 按顺序从左到右排.
        Top 列表用更低的不透明度 → 视觉上退到次要位置, 主计时还是视觉焦点.

        Top 列表是**逐项试放**的: 宽度放不下就停, 不会截出半个应用名.
        """
        pad = self._px("padding_x_px", 10)
        avail = max(20, limit_px - pad * 2)

        main = self._fit_text(self.build_text(st), avail)
        segments = [(main, self._text_color_rgb, 255)]
        width = self._measure(main)

        if not self.cfg.get("show_top_apps", True):
            return segments, width
        apps = st.get("top_apps") or []
        if not apps:
            return segments, width

        try:
            count = max(1, int(self.cfg.get("top_apps_count", 3)))
        except Exception:
            count = 3
        gap = str(self.cfg.get("top_apps_gap", "   "))
        sep = str(self.cfg.get("top_apps_item_sep", " · "))
        use_long = str(self.cfg.get("top_apps_time_format", "compact")).lower() == "long"

        cur = st.get("process")
        if cur and not self.cfg.get("top_apps_include_current", False):
            apps = [a for a in apps if str(a.get("name")) != str(cur)]
        if not apps:
            return segments, width

        budget = avail - width - self._measure(gap)
        if budget <= 0:
            return segments, width

        items = []
        for a in apps[:count]:
            name = self._short_name(a.get("name"))
            t = str(a.get("time_long" if use_long else "time") or "").strip()
            piece = f"{name} {t}".strip()
            if not piece:
                continue
            if self._measure(sep.join(items + [piece])) <= budget:
                items.append(piece)
            else:
                break
        if items:
            tail = gap + sep.join(items)
            try:
                alpha = max(0, min(255, int(self.cfg.get("dim_alpha", 150))))
            except Exception:
                alpha = 150
            segments.append((tail, self._text_color_rgb, alpha))
            width += self._measure(tail)
        return segments, width

    def _resolve_text_color(self) -> None:
        tc = str(self.cfg.get("text_color", "auto")).lower()
        rgb = None
        if tc != "auto":
            rgb = parse_color(tc)
        if rgb is None:
            rgb = (26, 26, 26) if system_uses_light_theme() else (230, 230, 230)
        # 存成 (r, g, b), 渲染时再转 BGR 序
        self._text_color_rgb = rgb

    # ---------- 渲染 ----------
    def _ensure_surface(self, w: int, h: int) -> None:
        if w == self._w and h == self._h and self._memdc:
            return
        self._release_surface()
        self._w, self._h = w, h
        self._memdc = gdi32.CreateCompatibleDC(self._screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h          # 负数 = top-down, 行序和数组下标一致
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0      # BI_RGB
        bits = ctypes.c_void_p()
        self._dib = gdi32.CreateDIBSection(self._screen_dc, ctypes.byref(bmi),
                                           DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        if not self._dib:
            log(f"CreateDIBSection failed err={ctypes.get_last_error()}")
            return
        self._bits = bits
        self._buf = (ctypes.c_uint32 * (w * h)).from_address(bits.value)
        self._old_bmp = gdi32.SelectObject(self._memdc, self._dib)
        self._bg_cache = None
        # 文字用 ANTIALIASED (灰度抗锯齿). 绝不能用 ClearType —
        # 亚像素渲染会让 R/G/B 三个通道不相等, 破坏"R 通道 = 覆盖率"的前提.
        gdi32.SetBkMode(self._memdc, TRANSPARENT)

    def _release_surface(self) -> None:
        if self._memdc and self._old_bmp:
            gdi32.SelectObject(self._memdc, self._old_bmp)
            self._old_bmp = 0
        if self._dib:
            gdi32.DeleteObject(self._dib)
            self._dib = 0
        if self._memdc:
            gdi32.DeleteDC(self._memdc)
            self._memdc = 0
        self._buf = None
        self._bits = None
        self._bg_cache = None
        self._w = self._h = 0

    def _build_bg(self, w: int, h: int):
        """预乘 BGRA 的圆角底, 只在尺寸变化时算一次."""
        if not self.cfg.get("background"):
            return None
        if self._bg_cache and self._bg_cache[0] == w and self._bg_cache[1] == h:
            return self._bg_cache[2]
        rgb = parse_color(str(self.cfg.get("bg_color", "#1C1C1C"))) or (28, 28, 28)
        alpha = max(0, min(255, int(self.cfg.get("bg_alpha", 90))))
        rad = max(0, min(self._px("corner_radius_px", 6), min(w, h) // 2))
        r, g, b = rgb
        pr, pg, pb = r * alpha // 255, g * alpha // 255, b * alpha // 255
        arr = (ctypes.c_uint32 * (w * h))()
        for y in range(h):
            for x in range(w):
                inside = True
                if rad > 0:
                    cx = cy = None
                    if x < rad and y < rad:
                        cx, cy = rad, rad
                    elif x >= w - rad and y < rad:
                        cx, cy = w - rad - 1, rad
                    elif x < rad and y >= h - rad:
                        cx, cy = rad, h - rad - 1
                    elif x >= w - rad and y >= h - rad:
                        cx, cy = w - rad - 1, h - rad - 1
                    if cx is not None and (x - cx) ** 2 + (y - cy) ** 2 > rad * rad:
                        inside = False
                if inside:
                    arr[y * w + x] = (alpha << 24) | (pr << 16) | (pg << 8) | pb
        self._bg_cache = (w, h, arr)
        return arr

    def render(self, segments) -> bool:
        """把各段文字画进 DIB 并 UpdateLayeredWindow 到窗口.

        segments = [(text, (r,g,b), alpha_mul)], 从左到右排.

        逐段渲染: 每段单独在黑底 DIB 上画白字拿到覆盖率(R 通道), 再按**该段自己的**
        颜色和不透明度合成到输出缓冲. 这样做多段不同颜色/透明度, 又不用两次 GDI 混合.
        """
        w, h = self._w, self._h
        if not (w > 0 and h > 0 and self._buf):
            return False
        n = w * h
        bg = self._build_bg(w, h)
        out = (ctypes.c_uint32 * n)()
        if bg is not None:
            ctypes.memmove(out, bg, n * 4)

        if self._font:
            gdi32.SelectObject(self._memdc, self._font)
        gdi32.SetTextColor(self._memdc, 0x00FFFFFF)
        y_off = self._px("text_offset_y_px", 0)
        buf = self._buf

        line_w = sum(self._measure(t) for t, _, _ in segments)
        x = max(0, (w - line_w) // 2)

        for text, (cr, cg, cb), amul in segments:
            if not text:
                continue
            ctypes.memset(self._bits, 0, n * 4)
            sz = SIZE()
            gdi32.GetTextExtentPoint32W(self._memdc, text, len(text), ctypes.byref(sz))
            ty = max(0, (h - sz.cy) // 2) + y_off
            rect = wintypes.RECT(x, ty, w, h)
            user32.DrawTextW(self._memdc, text, len(text), ctypes.byref(rect),
                             DT_LEFT | DT_TOP | DT_SINGLELINE | DT_NOPREFIX)

            # 只扫这段文字的包围盒 (外面一定是 0), 大窗口下能省一大半时间
            x0, x1 = max(0, x - 2), min(w, x + sz.cx + 3)
            y0, y1 = max(0, ty - 2), min(h, ty + sz.cy + 3)
            for yy in range(y0, y1):
                row = yy * w
                for xx in range(x0, x1):
                    i = row + xx
                    cov = buf[i] & 0xFF
                    if cov == 0:
                        continue
                    if amul < 255:
                        cov = cov * amul // 255
                        if cov == 0:
                            continue
                    inv = 255 - cov
                    px = out[i]
                    out[i] = (((cov + ((px >> 24) & 0xFF) * inv // 255) << 24)
                              | ((cr * cov // 255 + ((px >> 16) & 0xFF) * inv // 255) << 16)
                              | ((cg * cov // 255 + ((px >> 8) & 0xFF) * inv // 255) << 8)
                              | (cb * cov // 255 + (px & 0xFF) * inv // 255))
            x += self._measure(text)

        ctypes.memmove(self._bits, out, n * 4)

        # 贴到窗口
        # ⚠️ pptDst 必须传 NULL. 对 WS_CHILD 窗口传屏幕坐标会被再叠加一次父窗口偏移
        # (实测: 子窗口 y=0 -> 屏幕 1528, 传 (x,1528) 后变成 3056), 窗口直接跑到屏幕外.
        # MSDN: 位置没变化时 pptDst 可以传 NULL. 位置由 SetWindowPos 负责.
        size = SIZE(w, h)
        src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        ok = user32.UpdateLayeredWindow(self.hwnd, self._screen_dc, None,
                                        ctypes.byref(size), self._memdc, ctypes.byref(src),
                                        0, ctypes.byref(blend), ULW_ALPHA)
        if not ok:
            log(f"UpdateLayeredWindow failed err={ctypes.get_last_error()}")
        return bool(ok)

    # ---------- 布局 ----------
    def layout_and_paint(self, st: dict) -> bool:
        if not self._docked and not self.dock():
            return False
        self._ensure_font()
        self._resolve_text_color()

        pad = self._px("padding_x_px", 10)
        min_w = self._px("min_width_px", 60)
        want = self._px("max_width_px", 460)

        ranges = self._free_ranges()
        target = self._choose_range(ranges)
        if target is None:
            log("no free range on taskbar")
            return False

        # 宽度上限 = min(配置上限, 目标空闲段的宽度) —— 先定位置再裁内容
        limit = max(min_w, min(want, target[1] - target[0]))
        segments, line_w = self._build_segments(st, limit)
        width = max(min_w, min(line_w + pad * 2, limit))

        cr = wintypes.RECT()
        user32.GetClientRect(self.taskbar, ctypes.byref(cr))
        height = (cr.bottom - cr.top) or 48

        br = wintypes.RECT()
        user32.GetWindowRect(self.taskbar, ctypes.byref(br))

        x_screen = self._anchor_x(target, width)
        # 夹进目标空闲段: 既不压到任务按钮/托盘, 也不会跑出任务栏
        lo = max(br.left, target[0])
        hi = min(br.right, target[1]) - width
        x_screen = int(max(lo, min(int(x_screen), hi))) if hi >= lo else int(lo)

        # 屏幕 x -> 任务栏客户区 x (子窗口坐标是相对父窗口的)
        pt = POINT(x_screen, br.top)
        user32.ScreenToClient(self.taskbar, ctypes.byref(pt))

        resized = (width != self._w or height != self._h)
        if resized:
            self._ensure_surface(width, height)

        user32.SetWindowPos(self.hwnd, None, pt.x, 0, width, height,
                            SWP_NOZORDER | SWP_NOACTIVATE)

        # 只在"内容或画布变了"时重渲染
        key = tuple(segments)
        if key != self._seg_key or resized or not self._visible:
            self.render(segments)
            self._seg_key = key
            self._text = "".join(t for t, _, _ in segments)
        if not self._visible:
            self._show(True)
        self._write_status(segments, width, height, x_screen)
        return True

    def _write_status(self, segments, width: int, height: int, x_screen: int) -> None:
        """写一份当前状态, 方便用 --check 或外部脚本核对 HUD 到底显示了什么.

        窗口里的字是画进分层位图的, 读不出来, 所以只能自己记一份.
        """
        try:
            st = {
                "text": "".join(t for t, _, _ in segments),
                "segments": [
                    {"text": t, "color": list(c), "alpha": a} for t, c, a in segments
                ],
                "hwnd": f"{self.hwnd:#x}",
                "docked": self._docked,
                "taskbar": f"{self.taskbar:#x}",
                "x_screen": int(x_screen),
                "width": int(width),
                "height": int(height),
                "anchor": self.cfg.get("anchor"),
                "duration_mode": self.cfg.get("duration_mode"),
                "dpi_scale": round(self._dpi_scale(), 3),
                "updated_at": time.time(),
            }
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = STATUS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(tmp, STATUS_PATH)
        except Exception as e:
            log(f"write_status err: {e}")

    # ---------- 交互 ----------
    def _on_drag_start(self) -> None:
        p = POINT()
        user32.GetCursorPos(ctypes.byref(p))
        wr = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(wr))
        # moved=False: 光标没真正移动够就不算拖动.
        # 没有这个阈值的话, 单纯点一下也会写 hud_pos.json 并把 anchor 钉成 manual,
        # 位置就再也回不到自动贴边了.
        self._drag = {"cursor_x": p.x, "win_x": wr.left, "moved": False}
        user32.SetCapture(self.hwnd)

    def _on_drag_move(self) -> None:
        if not self._drag:
            return
        p = POINT()
        user32.GetCursorPos(ctypes.byref(p))
        dx = p.x - self._drag["cursor_x"]
        if not self._drag["moved"] and abs(dx) < self._px("drag_threshold_px", 4):
            return
        self._drag["moved"] = True
        self._manual_x = self._drag["win_x"] + dx
        self._reposition_to_manual()

    def _reposition_to_manual(self) -> None:
        br = wintypes.RECT()
        if not user32.GetWindowRect(self.taskbar, ctypes.byref(br)):
            return
        width = self._w
        x = max(br.left, min(int(self._manual_x), br.right - width))
        pt = POINT(x, br.top)
        user32.ScreenToClient(self.taskbar, ctypes.byref(pt))
        user32.SetWindowPos(self.hwnd, None, pt.x, 0, width, self._h,
                            SWP_NOZORDER | SWP_NOACTIVATE)

    def _on_drag_end(self) -> None:
        user32.ReleaseCapture()
        d = self._drag
        self._drag = None
        # 只有真的拖动过 (超过阈值) 才固化位置; 单纯点一下不改变 anchor
        if not d or not d.get("moved") or self._manual_x is None:
            return
        self.cfg["anchor"] = "manual"
        save_config(self.cfg)
        # 存夹取后的值, 免得留下 -29 这种越界坐标
        br = wintypes.RECT()
        x = int(self._manual_x)
        if user32.GetWindowRect(self.taskbar, ctypes.byref(br)):
            x = max(br.left, min(x, br.right - max(1, self._w)))
        self._manual_x = x
        save_pos({"x": x, "saved_at": time.time()})
        log(f"drag saved x={x}")

    def open_dashboard(self) -> None:
        import subprocess
        try:
            subprocess.Popen(paths.child_cmd("dashboard", "dashboard.py"),
                             cwd=ROOT, creationflags=0x08000000)
            log("dashboard launched")
        except Exception as e:
            log(f"dashboard failed: {e}")

    # ---------- 消息处理 ----------
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TIMER:
                self._on_tick()
                return 0
            if msg == TASKBAR_CREATED:
                # explorer 重建任务栏: 旧子 HWND 已被销毁, 必须重新停靠
                log("TaskbarCreated -> re-dock")
                self._docked = False
                self.taskbar = 0
                self._show(False)
                self.dock()
                self._seg_key = None
                self.layout_and_paint(read_state())
                return 0
            if msg == WM_DISPLAYCHANGE or msg == WM_DPICHANGED:
                # 分辨率/缩放变了: 任务栏几何变了, 重新停靠 + 重排
                log(f"display change (msg={msg:#x}) -> re-dock")
                self._docked = False
                self.taskbar = 0
                self._show(False)
                self.dock()
                self._seg_key = None
                self.layout_and_paint(read_state())
                return 0
            if msg == WM_SETTINGCHANGE:
                # 主题等设置变化: 不重新停靠(这个消息广播很频繁), 只重画
                self._seg_key = None
                self.layout_and_paint(read_state())
                return 0
            if msg == WM_LBUTTONDOWN:
                self._on_drag_start()
                return 0
            if msg == WM_MOUSEMOVE:
                self._on_drag_move()
                return 0
            if msg == WM_LBUTTONUP:
                self._on_drag_end()
                return 0
            if msg == WM_LBUTTONDBLCLK or msg == WM_RBUTTONUP:
                self.open_dashboard()
                return 0
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE      # 点我们不要抢走前台应用的焦点
            if msg == WM_PAINT or msg == WM_ERASEBKGND:
                return 0                  # 分层窗口不走 WM_PAINT
            if msg == WM_CLOSE:
                self.shutdown()
                return 0
            if msg == WM_DESTROY:
                user32.KillTimer(hwnd, TIMER_ID)
                user32.PostQuitMessage(0)
                return 0
            if msg == WM_NCDESTROY:
                self.hwnd = 0
        except Exception:
            log("wndproc err: " + traceback.format_exc())
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_tick(self) -> None:
        self._ticks += 1
        # 1) 关停信号 (app.py 退出时会写 control.json shutdown=1)
        if control_shutdown_requested() and not self._selftest:
            log("shutdown requested by control.json")
            self.shutdown()
            return
        # 2) 配置热重载
        fresh = load_config()
        if fresh != self.cfg:
            log("config changed -> reload")
            self.cfg = fresh
            self._font_key = None
            self._bg_cache = None
            # 字号/颜色这类变化不一定改到 segments, 必须显式让缓存失效
            self._seg_key = None
        if not self.cfg.get("enabled", True):
            log("disabled by config -> exit")
            self.shutdown()
            return
        # 3) 停靠健康检查
        every = max(1, int(self.cfg.get("health_every", 10)))
        if self._ticks % every == 0:
            tb = self._find_taskbar()
            if not tb or user32.GetParent(self.hwnd) != tb:
                log("health check: re-dock")
                self._docked = False
                self.taskbar = 0
                self.dock()
                self._seg_key = None
        # 4) 刷新文字
        st = read_state()
        self._last_state = st
        if st.get("paused") and self.cfg.get("hide_when_paused"):
            self._show(False)
            return
        try:
            self.layout_and_paint(st)
        except Exception:
            log("layout err: " + traceback.format_exc())

    # ---------- 生命周期 ----------
    def shutdown(self) -> None:
        log("shutdown")
        try:
            if self._memdc:
                self._release_surface()
            if self._font:
                gdi32.DeleteObject(self._font)
                self._font = 0
            if self._measure_dc:
                gdi32.DeleteDC(self._measure_dc)
                self._measure_dc = 0
            if self._screen_dc:
                user32.ReleaseDC(None, self._screen_dc)
                self._screen_dc = 0
            if self.hwnd:
                self.undock()
                user32.DestroyWindow(self.hwnd)
        except Exception:
            log("shutdown err: " + traceback.format_exc())

    def run(self, selftest: bool = False) -> int:
        self._selftest = selftest
        if not self.create_window():
            return 1
        # init trick: 用一次渲染把字体/画布建起来
        # 初次停靠失败不当致命错误: explorer 刚重启/正忙时 SetParent 会瞬时失败,
        # 交给 _on_tick 的健康检查持续重试 (每 health_every 次轮询一次).
        if not self.dock():
            log("初次停靠失败, 进入轮询后会持续重试")
        st = read_state()
        if self._docked and not self.layout_and_paint(st):
            log("first layout failed")
        if selftest:
            self._print_selftest()
            self.shutdown()
            return 0
        user32.SetTimer(self.hwnd, TIMER_ID, max(100, int(self.cfg.get("poll_ms", 500))), None)
        log("message loop start")
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        log("message loop end")
        return 0

    def _print_selftest(self) -> None:
        out = []
        wr = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(wr))
        cr = wintypes.RECT()
        user32.GetClientRect(self.taskbar, ctypes.byref(cr))
        tr = wintypes.RECT()
        user32.GetWindowRect(self.taskbar, ctypes.byref(tr))
        style = user32.GetWindowLongW(self.hwnd, GWL_STYLE)
        exstyle = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        # 统计非透明像素 + alpha 直方图.
        # alpha 直方图能证明"两段文字用了不同的不透明度"(主计时满不透明, Top 列表更淡),
        # 而不用去看屏幕.
        opaque = 0
        bbox = [10 ** 9, 10 ** 9, -1, -1]
        hist = {}
        if self._buf:
            w, h = self._w, self._h
            for y in range(h):
                row = y * w
                for x in range(w):
                    px = self._buf[row + x]
                    a = (px >> 24) & 0xFF
                    if a > 0:
                        opaque += 1
                        hist[a] = hist.get(a, 0) + 1
                        bbox[0] = min(bbox[0], x); bbox[1] = min(bbox[1], y)
                        bbox[2] = max(bbox[2], x); bbox[3] = max(bbox[3], y)
        top_alphas = sorted(hist.items(), key=lambda kv: -kv[1])[:6]
        out.append(f"text          = {self._text!r}")
        out.append(f"segments      = {[(t, c, a) for t, c, a in (self._seg_key or [])]}")
        out.append(f"hwnd          = {self.hwnd:#x}")
        out.append(f"taskbar       = {self.taskbar:#x}")
        out.append(f"parent == taskbar : {user32.GetParent(self.hwnd) == self.taskbar}")
        out.append(f"WS_CHILD set  : {bool(style & WS_CHILD)}   (style={style:#010x})")
        out.append(f"WS_EX_LAYERED : {bool(exstyle & WS_EX_LAYERED)}")
        out.append(f"window rect   = {(wr.left, wr.top, wr.right, wr.bottom)}  {wr.right-wr.left}x{wr.bottom-wr.top}")
        out.append(f"taskbar rect  = {(tr.left, tr.top, tr.right, tr.bottom)}  client={cr.right-cr.left}x{cr.bottom-cr.top}")
        out.append(f"inside taskbar: {tr.left <= wr.left and wr.right <= tr.right and tr.top <= wr.top and wr.bottom <= tr.bottom}")
        out.append(f"visible       : {bool(user32.IsWindowVisible(self.hwnd))}")
        out.append(f"surface size  = {self._w}x{self._h}")
        out.append(f"opaque pixels = {opaque} / {self._w * self._h}   text bbox={tuple(bbox) if opaque else None}")
        out.append(f"alpha hist    = {top_alphas}")
        out.append(f"anchor        = {self.cfg.get('anchor')!r}  manual_x={self._manual_x}")
        out.append(f"duration_mode = {self.cfg.get('duration_mode')!r}")
        out.append(f"free ranges   = {self._free_ranges()}")
        for line in out:
            print(line)
            log("selftest " + line)


def acquire_single_instance():
    """用命名互斥体防止起两份 HUD."""
    kernel32.CreateMutexW(None, False, "Local\\ScreenTimeTrackerTaskbarHud")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return True


def _hud_state():
    """只读地取一次运行中 HUD 的状态."""
    hwnd, tb = _find_hud_window()
    if not hwnd or not tb:
        return None
    wr, tr = wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    user32.GetWindowRect(tb, ctypes.byref(tr))
    return {
        "hwnd": hwnd,
        "taskbar": tb,
        "rect": (wr.left, wr.top, wr.right, wr.bottom),
        "parent": user32.GetParent(hwnd),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "inside": (tr.left <= wr.left and wr.right <= tr.right
                   and tr.top <= wr.top and wr.bottom <= tr.bottom),
    }


def test_recovery(hard: bool = False) -> int:
    """测两条恢复路径. 只对 HUD 自己的窗口动手, 不碰别的窗口.

    1) 广播 TaskbarCreated —— 这是 explorer 重建任务栏时真正会发的消息,
       用来验证"任务栏被重建后自动重新停靠".
    2) (--test-recovery-hard) 强行 SetParent(NULL) 把窗口摘下来, 等健康检查自愈.
       副作用: HUD 会短暂变成浮窗飘在任务栏上, 几秒内被自动收回.
    """
    st = _hud_state()
    if not st:
        print("HUD 没在运行, 先启动它再测")
        return 1
    print(f"初始: parent={_h(st['parent'])} rect={st['rect']} inside={st['inside']} visible={st['visible']}")
    ok = True

    print("测试1: 向 HUD 发 TaskbarCreated (模拟 explorer 重建任务栏)...")
    user32.PostMessageW(st["hwnd"], TASKBAR_CREATED, 0, 0)
    time.sleep(3)
    s1 = _hud_state()
    if s1 is None:
        print("  [FAIL] 窗口消失了")
        return 1
    good1 = s1["parent"] == s1["taskbar"] and s1["inside"] and s1["visible"]
    print(f"  {'[OK]' if good1 else '[FAIL]'} parent={_h(s1['parent'])} rect={s1['rect']} "
          f"inside={s1['inside']} visible={s1['visible']}")
    ok = ok and good1

    if hard:
        print("测试2: 强行 SetParent(NULL) 摘下窗口, 等健康检查自愈...")
        user32.SetParent(st["hwnd"], None)
        time.sleep(1)
        s_bad = _hud_state()
        if s_bad:
            print(f"  摘下来后: parent={_h(s_bad['parent'])} inside={s_bad['inside']}")
        time.sleep(9)
        s2 = _hud_state()
        good2 = bool(s2 and s2["parent"] == s2["taskbar"] and s2["inside"] and s2["visible"])
        if s2:
            print(f"  {'[OK]' if good2 else '[FAIL]'} 自愈后 parent={_h(s2['parent'])} "
                  f"rect={s2['rect']} inside={s2['inside']} visible={s2['visible']}")
        else:
            print("  [FAIL] 窗口消失了")
        ok = ok and good2

    print("结论:", "全部通过" if ok else "有失败项")
    return 0 if ok else 2


def _find_hud_window():
    """在任务栏的子窗口里按类名找 HUD.

    不能用 FindWindow: 我们的窗口是任务栏的 WS_CHILD 子窗口,
    既不参与顶层窗口枚举, 跨进程按局部注册的类名查找也不可靠.
    """
    tb = user32.FindWindowW("Shell_TrayWnd", None)
    if not tb:
        return 0, 0
    found = []

    def enum_cb(hwnd, lparam):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value == HUD_CLASS:
            found.append(hwnd)
            return False
        return True

    user32.EnumChildWindows(tb, ENUMWINDOWSPROC(enum_cb), 0)
    return (found[0] if found else 0), tb


def check_running_hud() -> int:
    """--check: 只读地看当前 HUD 挂在哪 (不创建也不修改任何窗口)."""
    hwnd, tb = _find_hud_window()
    if not hwnd:
        print(f"未运行: 任务栏子窗口里找不到类 {HUD_CLASS}")
        return 1
    wr, tr, cr = wintypes.RECT(), wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    user32.GetWindowRect(tb, ctypes.byref(tr))
    user32.GetClientRect(tb, ctypes.byref(cr))
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    print(f"hwnd          = {hwnd:#x}")
    print(f"parent        = {user32.GetParent(hwnd):#x}  (taskbar={tb:#x})")
    print(f"停靠在任务栏  = {user32.GetParent(hwnd) == tb}")
    print(f"WS_CHILD      = {bool(style & WS_CHILD)}")
    print(f"可见          = {bool(user32.IsWindowVisible(hwnd))}")
    print(f"窗口 rect     = {(wr.left, wr.top, wr.right, wr.bottom)}  {wr.right-wr.left}x{wr.bottom-wr.top}")
    print(f"任务栏 rect   = {(tr.left, tr.top, tr.right, tr.bottom)}  client={cr.right-cr.left}x{cr.bottom-cr.top}")
    print(f"在任务栏内    = "
          f"{tr.left <= wr.left and wr.right <= tr.right and tr.top <= wr.top and wr.bottom <= tr.bottom}")
    print(f"距任务栏右缘  = {tr.right - wr.right} px")
    try:
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            st = json.load(f)
        import datetime
        ts = datetime.datetime.fromtimestamp(st.get("updated_at", 0)).strftime("%H:%M:%S")
        print(f"当前显示文字  = {st.get('text')!r}   (更新于 {ts})")
        for i, seg in enumerate(st.get("segments") or []):
            print(f"  段{i+1}         = {seg.get('text')!r}  color={seg.get('color')} alpha={seg.get('alpha')}")
        print(f"anchor        = {st.get('anchor')!r}  duration_mode={st.get('duration_mode')!r}  dpi_scale={st.get('dpi_scale')}")
    except Exception as e:
        print(f"读 hud_status.json 失败: {e}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    # DPI 声明必须早于任何窗口操作, --check 也要, 否则读到的是 96 DPI 虚拟坐标
    awareness = set_dpi_awareness()
    if "--check" in args:
        return check_running_hud()
    if "--test-recovery" in args or "--test-recovery-hard" in args:
        return test_recovery(hard="--test-recovery-hard" in args)
    if not acquire_single_instance():
        log("another instance is running -> exit")
        print("another instance is running")
        return 0
    hud = TaskbarHud()
    hud.dpi_awareness = awareness
    log(f"dpi awareness = {awareness}")
    if "--selftest" in args:
        print(f"dpi awareness = {awareness}")
    try:
        return hud.run(selftest="--selftest" in args)
    except Exception:
        log("fatal: " + traceback.format_exc())
        print(traceback.format_exc())
        return 3


if __name__ == "__main__":
    sys.exit(main())
