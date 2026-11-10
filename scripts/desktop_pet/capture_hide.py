"""「看螢幕」截圖時暫時把桌寵自己的視窗藏起來，平常不藏。

2026-11-10 以前是開機就把桌寵視窗永久設成「排除在所有擷取之外」，
避免看螢幕功能拍到自己；副作用是使用者自己用截圖工具、OBS 錄影也都拍不到她。
現在改成：視窗只先登記起來，只在 screen_watch 擷取的那一瞬間排除，擷取完馬上恢復。

SetWindowDisplayAffinity 只要求視窗屬於同一個行程，不限執行緒，
所以 screen_watch 在背景執行緒呼叫也沒問題。任何 win32 呼叫失敗都安靜略過。
"""

import contextlib
import threading
import time

_WDA_NONE = 0x00000000
_WDA_EXCLUDEFROMCAPTURE = 0x00000011

# DWM 套用 affinity 大約要一個畫面更新週期，擷取前稍等一下才不會拍到半套。
_SETTLE_SEC = 0.05

_lock = threading.Lock()
_hwnds: set = set()


def register(hwnd: int) -> None:
    """登記一個「看螢幕截圖時要藏起來」的視窗。"""
    with _lock:
        _hwnds.add(hwnd)


def _set_affinity(hwnds, flag: int) -> None:
    import ctypes

    try:
        user32 = ctypes.windll.user32
    except Exception:
        return
    for h in hwnds:
        try:
            user32.SetWindowDisplayAffinity(h, flag)
        except Exception:
            pass


@contextlib.contextmanager
def hidden_from_capture():
    """with 區塊裡，登記過的視窗都不會出現在螢幕擷取裡。"""
    with _lock:
        hwnds = list(_hwnds)
    if not hwnds:
        yield
        return
    _set_affinity(hwnds, _WDA_EXCLUDEFROMCAPTURE)
    time.sleep(_SETTLE_SEC)
    try:
        yield
    finally:
        _set_affinity(hwnds, _WDA_NONE)
