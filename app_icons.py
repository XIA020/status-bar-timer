"""app_icons.py — 从 exe 里提取应用图标, 转成 PNG 给管理页面用.

做法 (不需要 COM vtable, 只用 pywin32 + ctypes):
    1. `SHDefExtractIconW` 按指定尺寸拿 HICON (能要到 64px, 比 ExtractIconEx 的 32px 清楚)
    2. 建一块 32 位 DIB, **先填白**再 DrawIconEx
       —— GDI 画图标走的是掩码, 透明区域会变成背景色; 而 DDB 的 alpha 通道不可靠.
          先填白就避开了 alpha 问题: 得到"白底 + 图标", 正好配页面里的白色圆角磁贴.
    3. GetDIBits 取 BGRA -> PIL -> PNG 字节

结果按 (exe 路径, mtime, 尺寸) 缓存在 data/icons/, 不会每次重启都重新抽.
抽不到就返回 None, 前端退化成"首字母圆形头像".
"""
from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes

import paths

ROOT = paths.app_dir()
ICON_DIR = os.path.join(ROOT, "data", "icons")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

DI_NORMAL = 0x0003
DIB_RGB_COLORS = 0

shell32.SHDefExtractIconW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.UINT,
                                      ctypes.POINTER(wintypes.HICON),
                                      ctypes.POINTER(wintypes.HICON), wintypes.UINT]
shell32.SHDefExtractIconW.restype = ctypes.c_long


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH,
                              wintypes.UINT]
user32.DestroyIcon.argtypes = [wintypes.HICON]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.PatBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.DWORD]
WHITENESS = 0x00FF0062


def _cache_path(exe: str, size: int) -> str:
    try:
        mtime = int(os.path.getmtime(exe))
    except OSError:
        mtime = 0
    key = hashlib.sha1(f"{exe.lower()}|{mtime}|{size}".encode("utf-8", "replace")).hexdigest()[:20]
    return os.path.join(ICON_DIR, f"{key}.png")


def extract_icon_png(exe_path: str, size: int = 64) -> bytes | None:
    """返回 PNG 字节, 失败返回 None. 带磁盘缓存."""
    if not exe_path or not os.path.isfile(exe_path):
        return None
    cache = _cache_path(exe_path, size)
    if os.path.isfile(cache):
        try:
            with open(cache, "rb") as f:
                return f.read()
        except OSError:
            pass

    png = _extract(exe_path, size)
    if png:
        try:
            os.makedirs(ICON_DIR, exist_ok=True)
            tmp = cache + ".tmp"
            with open(tmp, "wb") as f:
                f.write(png)
            os.replace(tmp, cache)
        except OSError:
            pass
    return png


def _extract(exe_path: str, size: int) -> bytes | None:
    large = wintypes.HICON()
    small = wintypes.HICON()
    # nIconSize: 低 16 位 = 想要的尺寸
    hr = shell32.SHDefExtractIconW(exe_path, 0, 0, ctypes.byref(large), ctypes.byref(small),
                                   (size & 0xFFFF))
    if hr != 0 or not large.value:
        # 退一步用 ExtractIconEx (只有 32px)
        try:
            import win32gui
            lg, _sm = win32gui.ExtractIconEx(exe_path, 0)
            if not lg:
                return None
            large = wintypes.HICON(lg[0])
        except Exception:
            return None

    screen_dc = user32.GetDC(None)
    mem_dc = 0
    dib = 0
    old = 0
    bits = ctypes.c_void_p()
    try:
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = size
        bmi.bmiHeader.biHeight = -size        # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        dib = gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                     ctypes.byref(bits), None, 0)
        if not dib:
            return None
        old = gdi32.SelectObject(mem_dc, dib)
        # 先铺白: GDI 画图标按掩码合成, 透明处会留下背景色
        gdi32.PatBlt(mem_dc, 0, 0, size, size, WHITENESS)
        if not user32.DrawIconEx(mem_dc, 0, 0, large, size, size, 0, None, DI_NORMAL):
            return None

        buf = ctypes.cast(bits, ctypes.POINTER(ctypes.c_ubyte * (size * size * 4))).contents
        raw = bytes(buf)
        try:
            from PIL import Image
        except Exception:
            return None
        img = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1)
        img = img.convert("RGB")           # 白底, 不需要 alpha
        import io
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()
    except Exception:
        return None
    finally:
        try:
            if large.value:
                user32.DestroyIcon(large)
            if small.value:
                user32.DestroyIcon(small)
        except Exception:
            pass
        if mem_dc:
            if old:
                gdi32.SelectObject(mem_dc, old)
            if dib:
                gdi32.DeleteObject(dib)
            gdi32.DeleteDC(mem_dc)
        if screen_dc:
            user32.ReleaseDC(None, screen_dc)


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        data = extract_icon_png(p)
        print(f"{p}: {'OK ' + str(len(data)) + ' bytes' if data else 'FAIL'}")
