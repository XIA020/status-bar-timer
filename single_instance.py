"""Windows 单实例互斥锁，避免同一份数据被多个进程同时采集。"""
from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes

_ERROR_ALREADY_EXISTS = 183
_held_handles = []


def acquire_instance_lock(role: str, root: str) -> bool:
    """当前进程获得指定安装目录和角色的互斥锁时返回 True。"""
    identity = os.path.normcase(os.path.abspath(root)).encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()[:16]
    name = rf"Local\StatusBarTimer.{suffix}.{role}"

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False

    _held_handles.append((kernel32, handle))
    return True
