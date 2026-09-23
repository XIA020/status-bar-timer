"""diag_child_pos.py — 定位子窗口坐标翻倍问题.

假设: UpdateLayeredWindow 的 pptDst 对子窗口会重新定位窗口,
传屏幕坐标 (0,1528) 会被再叠加一层父窗口偏移 -> 1528+1528=3056.

本脚本依次测试:
  A. 停靠后 SetWindowPos(0,0,100,40), 打印 rect            (基准)
  B. 调 ULW(pptDst = 屏幕坐标), 再打印 rect                (怀疑会跑掉)
  C. 调 ULW(pptDst = NULL),      再打印 rect                (预期正常)
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.SetParent.restype = wintypes.HWND
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
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
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT),
                                       ctypes.POINTER(SIZE), wintypes.HDC, ctypes.POINTER(POINT),
                                       wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
user32.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
GWL_STYLE = -16
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
ULW_ALPHA = 2


def r(hwnd):
    x = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(x))
    return (x.left, x.top, x.right, x.bottom)


def main():
    print("dpi aware:", bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))))
    tb = user32.FindWindowW("Shell_TrayWnd", None)
    print("taskbar rect :", r(tb))
    c0 = POINT(0, 0)
    user32.ClientToScreen(tb, ctypes.byref(c0))
    print("client origin:", (c0.x, c0.y))
    p = POINT(0, r(tb)[1])
    user32.ScreenToClient(tb, ctypes.byref(p))
    print("ScreenToClient(tb, (0,tbTop)) =", (p.x, p.y))

    hinst = kernel32.GetModuleHandleW(None)
    cls = "DiagChildPos"

    def wp(h, m, w, l):
        return user32.DefWindowProcW(h, m, w, l)

    keep = WNDPROC(wp)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = keep
    wc.hInstance = hinst
    wc.lpszClassName = cls
    user32.RegisterClassExW(ctypes.byref(wc))

    W, H = 200, 40
    ch = user32.CreateWindowExW(WS_EX_LAYERED | WS_EX_NOACTIVATE, cls, "d", WS_POPUP,
                                -4000, -4000, W, H, None, None, hinst, None)
    style = user32.GetWindowLongW(ch, GWL_STYLE)
    user32.SetWindowLongW(ch, GWL_STYLE, (style & ~WS_POPUP) | WS_CHILD)
    user32.SetParent(ch, tb)

    # 准备 DIB
    sdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(sdc)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = W
    bmi.bmiHeader.biHeight = -H
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(sdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    gdi32.SelectObject(mdc, hbmp)
    ctypes.memset(bits, 0x80, W * H * 4)      # 半透明填充, 肉眼可见

    size = SIZE(W, H)
    src = POINT(0, 0)
    blend = BLENDFUNCTION(0, 0, 255, 1)

    try:
        user32.SetWindowPos(ch, None, 500, 0, W, H,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        print("A  base (child coords 500,0)      :", r(ch))

        wr = wintypes.RECT()
        user32.GetWindowRect(ch, ctypes.byref(wr))
        dst = POINT(wr.left, wr.top)
        ctypes.set_last_error(0)
        ok = user32.UpdateLayeredWindow(ch, sdc, ctypes.byref(dst), ctypes.byref(size),
                                        mdc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
        print(f"B  ULW(pptDst=screen {dst.x},{dst.y}) ok={ok} ->", r(ch))

        user32.SetWindowPos(ch, None, 500, 0, W, H, SWP_NOZORDER | SWP_NOACTIVATE)
        ctypes.set_last_error(0)
        ok = user32.UpdateLayeredWindow(ch, sdc, None, ctypes.byref(size),
                                        mdc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
        print(f"C  ULW(pptDst=NULL) ok={ok} err={ctypes.get_last_error()} ->", r(ch))

        user32.SetWindowPos(ch, None, 800, 0, W, H, SWP_NOZORDER | SWP_NOACTIVATE)
        ctypes.set_last_error(0)
        ok = user32.UpdateLayeredWindow(ch, sdc, None, ctypes.byref(size),
                                        mdc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
        print(f"D  move to 800 then ULW(NULL) ok={ok} ->", r(ch))
    finally:
        user32.SetWindowPos(ch, None, 0, 0, 0, 0,
                            SWP_NOMOVE if False else 0x0002 | 0x0001 | SWP_NOZORDER | SWP_NOACTIVATE | 0x0080)
        user32.SetParent(ch, None)
        user32.DestroyWindow(ch)
        print("cleaned up")


if __name__ == "__main__":
    main()
