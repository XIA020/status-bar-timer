"""probe_taskbar_dock.py — 只读探测: 任务栏结构 + 停靠/分层窗口可行性.

写正式 HUD 之前先确认这台机器上:
  1. Shell_TrayWnd / TrayNotifyWnd / 任务列表等子窗口的类名与矩形
  2. 任务栏 DPI 缩放
  3. 子窗口 SetParent 到任务栏能否成功
  4. UpdateLayeredWindow 在 WS_CHILD 窗口上能否成功
     (决定渲染走「逐像素 alpha」还是退回「色键」)

只读探测 + 一个立即销毁的隐藏窗口, 不修改任何系统设置, 不动任何已有窗口.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

GWL_STYLE = -16
GWL_EXSTYLE = -20

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
DIB_RGB_COLORS = 0

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

GW_CHILD = 5
GW_HWNDNEXT = 2


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


# ---- argtypes (64 位下句柄必须声明, 否则被截断成 32 位) ----
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.SetParent.restype = wintypes.HWND
user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT),
                                       ctypes.POINTER(SIZE), wintypes.HDC, ctypes.POINTER(POINT),
                                       wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]

# 这些返回句柄/指针, 不声明 restype 会被截断成 32 位
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def rect_of(hwnd):
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right, r.bottom)


def dump_tree(hwnd, depth=0, max_depth=4, out=None):
    if out is None:
        out = []
    if depth > max_depth:
        return out
    child = user32.GetWindow(child_of := hwnd, GW_CHILD)
    while child:
        r = rect_of(child)
        vis = bool(user32.IsWindowVisible(child))
        out.append(("  " * depth) + f"{class_name(child):<38} vis={int(vis)} rect={r}")
        dump_tree(child, depth + 1, max_depth, out)
        child = user32.GetWindow(child, GW_HWNDNEXT)
    return out


def main() -> int:
    print("=" * 78)
    print("1) 任务栏窗口")
    print("=" * 78)
    taskbar = user32.FindWindowW("Shell_TrayWnd", None)
    if not taskbar:
        print("FAIL: 找不到 Shell_TrayWnd")
        return 1
    print(f"Shell_TrayWnd hwnd = {taskbar:#x}")
    print(f"  window rect = {rect_of(taskbar)}")
    cr = wintypes.RECT()
    user32.GetClientRect(taskbar, ctypes.byref(cr))
    print(f"  client rect = {(cr.left, cr.top, cr.right, cr.bottom)}  ({cr.right-cr.left}x{cr.bottom-cr.top})")
    pt = POINT(rect_of(taskbar)[0], rect_of(taskbar)[1])
    user32.ScreenToClient(taskbar, ctypes.byref(pt))
    print(f"  客户区原点(屏幕坐标) = ({pt.x}, {pt.y})")

    hdc_screen = user32.GetDC(0)
    dpi = gdi32.GetDeviceCaps(hdc_screen, 88)  # LOGPIXELSX
    print(f"  screen LOGPIXELSX = {dpi}  (scale={dpi/96:.2f})")
    user32.ReleaseDC(0, hdc_screen)

    tray = user32.FindWindowExW(taskbar, None, "TrayNotifyWnd", None)
    print(f"  TrayNotifyWnd hwnd = {tray:#x}  rect = {rect_of(tray)}")

    print()
    print("=" * 78)
    print("2) 任务栏子窗口树 (用于判断哪里是空闲区)")
    print("=" * 78)
    for line in dump_tree(taskbar, 0, 3):
        print("  " + line)

    print()
    print("=" * 78)
    print("3) POC: 创建一个隐藏子窗口, SetParent 到任务栏, 测试 UpdateLayeredWindow")
    print("=" * 78)

    hinst = kernel32.GetModuleHandleW(None)
    cls_name = "ProbeTaskbarDockWnd"

    def wndproc(hwnd, msg, wparam, lparam):
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.style = 0
    wc.lpfnWndProc = WNDPROC(wndproc)
    wc.hInstance = hinst
    wc.lpszClassName = cls_name
    atom = user32.RegisterClassExW(ctypes.byref(wc))
    print(f"RegisterClassExW -> atom={atom} err={ctypes.get_last_error()}")

    W, H = 120, 30
    hwnd = user32.CreateWindowExW(
        WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
        cls_name, "probe", WS_POPUP,
        -4000, -4000, W, H, None, None, hinst, None)
    print(f"CreateWindowExW -> hwnd={hwnd:#x} err={ctypes.get_last_error()}")
    if not hwnd:
        print("FAIL: 窗口创建失败, 后续测试中止")
        return 1

    try:
        # 1) 样式改造 + SetParent
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        print(f"before: style={style:#010x}")
        style = (style & ~WS_POPUP) | WS_CHILD
        ctypes.set_last_error(0)
        prev = user32.SetWindowLongW(hwnd, GWL_STYLE, style)
        print(f"SetWindowLongW(WS_CHILD) -> prev={prev:#x} err={ctypes.get_last_error()}")

        ctypes.set_last_error(0)
        old_parent = user32.SetParent(hwnd, taskbar)
        err = ctypes.get_last_error()
        print(f"SetParent -> old_parent={old_parent:#x} err={err}")
        print(f"GetParent  -> {user32.GetParent(hwnd):#x}  (taskbar={taskbar:#x})")
        dock_ok = user32.GetParent(hwnd) == taskbar
        print(f"[{'OK' if dock_ok else 'FAIL'}] 子窗口停靠任务栏")

        # 2) 定位到任务栏客户区
        user32.SetWindowPos(hwnd, None, 0, 0, W, H,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        print(f"SetWindowPos -> rect={rect_of(hwnd)}")

        # 3) UpdateLayeredWindow on a WS_CHILD window
        hdc_screen = user32.GetDC(0)
        memdc = gdi32.CreateCompatibleDC(hdc_screen)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = W
        bmi.bmiHeader.biHeight = -H          # 负数 = top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0      # BI_RGB
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), DIB_RGB_COLORS,
                                      ctypes.byref(bits), None, 0)
        print(f"CreateDIBSection -> hbmp={hbmp:#x} bits={bits.value is not None}")
        old_bmp = gdi32.SelectObject(memdc, hbmp)

        size = SIZE(W, H)
        src = POINT(0, 0)
        dst = POINT(rect_of(hwnd)[0], rect_of(hwnd)[1])
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        ctypes.set_last_error(0)
        ok = user32.UpdateLayeredWindow(hwnd, hdc_screen, ctypes.byref(dst),
                                        ctypes.byref(size), memdc, ctypes.byref(src),
                                        0, ctypes.byref(blend), ULW_ALPHA)
        err = ctypes.get_last_error()
        print(f"UpdateLayeredWindow(child) -> ok={ok} err={err} ({ctypes.FormatError(err) if err else 'no error'})")
        print(f"[{'OK' if ok else 'FAIL'}] WS_CHILD 窗口上逐像素 alpha")

        gdi32.SelectObject(memdc, old_bmp)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(0, hdc_screen)
    finally:
        # 解挂顺序: 隐藏 -> SetParent(null) -> 样式还原 -> 销毁
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | 0x0080)
        user32.SetParent(hwnd, None)
        user32.DestroyWindow(hwnd)
        print("探测窗口已销毁")

    print()
    print("结论: 看上面 [OK]/[FAIL] 两行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
