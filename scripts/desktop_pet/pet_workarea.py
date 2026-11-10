"""取得某個螢幕座標所在螢幕的工作區（扣掉工作列）。失敗時退回整個螢幕。"""

import ctypes
from ctypes import wintypes


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def work_area_at(x: int, y: int, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """回傳 (left, top, right, bottom)。fallback 通常是 (0, 0, 螢幕寬, 螢幕高)。"""
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        mon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)  # 2 = 找最近的螢幕
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return fallback
