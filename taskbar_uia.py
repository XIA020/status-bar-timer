"""Read Windows 11 taskbar control rectangles without loading a managed runtime."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from contextlib import contextmanager
import threading
import time
from uuid import UUID


# Native COM interface slots from the Windows SDK UIAutomationClient.h.
_RELEASE = 2
_ELEMENT_FROM_HANDLE = 6
_CREATE_CACHE = 20
_CREATE_TRUE_CONDITION = 21
_ADD_PROPERTY = 3
_FIND_ALL_BUILD_CACHE = 8
_ARRAY_LENGTH = 3
_ARRAY_ELEMENT = 4
_CACHED_CONTROL_TYPE = 53
_CACHED_CLASS_NAME = 62
_CACHED_IS_OFFSCREEN = 70
_CACHED_BOUNDS = 75
_CACHE_PROPERTIES = (30001, 30003, 30012, 30022)  # bounds, type, class, offscreen
_BUTTON_TYPES = {50000, 50002, 50013, 50031}  # button, checkbox, radio, split button
_CLSID = (C.c_byte * 16).from_buffer_copy(UUID("ff48dba4-60ef-4201-aa87-54103eef594e").bytes_le)
_IID = (C.c_byte * 16).from_buffer_copy(UUID("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee").bytes_le)

_ole32 = C.WinDLL("ole32")
_ole32.CoInitializeEx.argtypes = (C.c_void_p, W.DWORD)
_ole32.CoCreateInstance.argtypes = (C.c_void_p, C.c_void_p, C.c_ulong,
                                  C.c_void_p, C.POINTER(C.c_void_p))
_oleaut32 = C.WinDLL("oleaut32")
_oleaut32.SysFreeString.argtypes = (C.c_void_p,)
_oleaut32.SysFreeString.restype = None


def _call(obj, slot, *args, result=C.HRESULT):
    table = C.cast(obj, C.POINTER(C.POINTER(C.c_void_p))).contents
    signature = C.WINFUNCTYPE(result, C.c_void_p, *(type(arg) for arg in args))
    return signature(table[slot])(obj, *args)


@contextmanager
def _interface():
    ptr = C.c_void_p()
    try:
        yield ptr
    finally:
        if ptr:
            _call(ptr, _RELEASE, result=C.c_ulong)


def _snapshot(hwnd, taskbar):
    left, top, right, bottom = taskbar
    with _interface() as automation, _interface() as root, _interface() as cache, \
            _interface() as condition, _interface() as elements:
        hr = _ole32.CoCreateInstance(_CLSID, None, 1, _IID, C.byref(automation))
        if hr < 0:
            raise OSError(f"CoCreateInstance HRESULT {hr:#x}")
        _call(automation, _ELEMENT_FROM_HANDLE, W.HWND(hwnd), C.pointer(root))
        _call(automation, _CREATE_CACHE, C.pointer(cache))
        for prop in _CACHE_PROPERTIES:
            _call(cache, _ADD_PROPERTY, C.c_int(prop))
        _call(automation, _CREATE_TRUE_CONDITION, C.pointer(condition))
        _call(root, _FIND_ALL_BUILD_CACHE, C.c_int(4), condition, cache, C.pointer(elements))
        if not elements:
            return None
        count = C.c_int()
        _call(elements, _ARRAY_LENGTH, C.pointer(count))
        buttons = []
        tray_left = right
        for index in range(count.value):
            with _interface() as element:
                _call(elements, _ARRAY_ELEMENT, C.c_int(index), C.pointer(element))
                offscreen, bounds = W.BOOL(), W.RECT()
                _call(element, _CACHED_IS_OFFSCREEN, C.pointer(offscreen))
                if offscreen.value:
                    continue
                _call(element, _CACHED_BOUNDS, C.pointer(bounds))
                start, end = max(left, bounds.left), min(right, bounds.right)
                if end <= start or bounds.bottom <= top or bounds.top >= bottom:
                    continue
                name = C.c_void_p()
                _call(element, _CACHED_CLASS_NAME, C.pointer(name))
                try:
                    cls = C.wstring_at(name.value) if name else ""
                finally:
                    _oleaut32.SysFreeString(name)
                if cls.startswith("SystemTray."):
                    tray_left = min(tray_left, start)
                    continue
                kind = C.c_int()
                _call(element, _CACHED_CONTROL_TYPE, C.pointer(kind))
                if (kind.value in _BUTTON_TYPES or cls == "ToggleButton"
                        or cls == "Taskbar.TaskListButtonAutomationPeer"):
                    buttons.append((start, end))
        if tray_left < right:
            return buttons + [(tray_left, right)]
        return None


class TaskbarControls:
    """Refresh rectangles off the HUD thread. Discard stale shell coordinates."""

    def __init__(self):
        self._target = None
        self._result = None
        self._thread = None

    def read(self, hwnd, rect):
        target = (hwnd, (rect.left, rect.top, rect.right, rect.bottom))
        self._target = target
        if self._thread is None:
            self._thread = threading.Thread(target=self._poll, name="taskbar-uia", daemon=True)
            self._thread.start()
        result = self._result
        if result and result[0] == target and time.monotonic() - result[1] < 2:
            return result[2]
        return None

    def _poll(self):
        if _ole32.CoInitializeEx(None, 0) < 0:  # MTA
            return
        try:
            while True:
                target = self._target
                started = time.monotonic()
                try:
                    ranges = _snapshot(*target)
                except OSError:  # Explorer can disappear or recreate controls during a query.
                    ranges = None
                self._result = (target, started, ranges)
                time.sleep(0.4)
        finally:
            _ole32.CoUninitialize()
