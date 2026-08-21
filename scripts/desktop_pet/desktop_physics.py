"""桌寵物理：讓桌寵感覺到「桌面」的存在——會因為重力落下，落在工作列或
其他視窗的上緣，不會呆呆懸空飄在螢幕中間。第一階段只做重力＋落地偵測，
沿用現有的靜態立繪，不需要新的分格動畫素材（規劃討論見對話紀錄，暫未寫進
TTS通用平台_專案規劃.md）。

落地面的判定完全基於「目前看得到的視窗」，每個 tick 都重新算一次、不快取——
使用者隨時可能開新視窗、關視窗、移動視窗，快取只會讓桌寵卡在已經不存在的
平台上。這個「重算」的副作用剛好也是想要的行為：她站的視窗被關掉或移走，
下一 tick 找不到那個平台了，會自動開始往下一個平台掉，不用額外寫「平台消失」
的特殊邏輯。
"""
import ctypes
from ctypes import wintypes
from typing import Optional

import win32gui

_SPI_GETWORKAREA = 0x0030

_MIN_SURFACE_WIDTH = 60  # 太窄的視窗（例如工具列上的小面板）不當落腳點，避免立繪比落點還寬還卡在邊緣


def work_area_bottom() -> int:
    """工作列上緣的 y 座標（可用桌面高度下緣）——當作最終地板，找不到其他
    視窗可以站的時候一定有這個當底。"""
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(_SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return rect.bottom


def _visible_window_rects(exclude_hwnd: Optional[int]) -> list[tuple[int, int, int, int]]:
    """列出目前所有「看起來像正常應用程式視窗」的可見視窗範圍，排除桌寵自己。
    這裡落點抓寬鬆一點沒關係（跟 screen_watch.py 的黑名單邏輯不同，站錯地方
    最多就是懸空感覺怪，不像螢幕讀取那樣有隱私疑慮），只濾掉最小化、沒標題、
    太窄的視窗。"""
    rects: list[tuple[int, int, int, int]] = []

    def _callback(hwnd: int, _extra) -> bool:
        if hwnd == exclude_hwnd:
            return True
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return True
        if not win32gui.GetWindowText(hwnd):
            return True
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right - left < _MIN_SURFACE_WIDTH:
            return True
        rects.append((left, top, right, bottom))
        return True

    win32gui.EnumWindows(_callback, None)
    return rects


def find_landing_y(char_left: int, char_right: int, char_bottom: int, exclude_hwnd: Optional[int]) -> int:
    """找角色目前這個 x 範圍正下方最近的落腳面 y 座標（角色底部該貼齊的 y）。
    候選是跟角色左右範圍有重疊、頂邊在角色目前底部之下（或非常接近）的視窗
    頂邊，取最靠近的那個；都沒有的話回退到工作列上緣（最終地板）。"""
    best = work_area_bottom()
    for left, top, right, _bottom in _visible_window_rects(exclude_hwnd):
        if not (left < char_right and right > char_left):
            continue  # x 範圍沒重疊，不是正下方
        if top < char_bottom - 4:
            continue  # 頂邊已經在角色上方，角色其實卡在裡面了，不當落點
        if top < best:
            best = top
    return best
