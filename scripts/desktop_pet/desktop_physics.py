"""桌寵物理模擬。

目的是讓桌寵感覺到「桌面」的存在：她會因為重力落下，並且落在工作列或
其他視窗的上緣，而不是呆呆懸空飄在螢幕中間。

目前是第一階段實作，只做「重力」＋「落地偵測」，沿用現有的靜態立繪，
不需要額外的分格動畫素材（更完整的規劃討論見對話紀錄，暫時還沒寫進
TTS通用平台_專案規劃.md）。

## 落地面判定的設計原則

落地面的判定完全基於「目前看得到的視窗」，而且每個 tick 都重新計算一次、
完全不快取結果。原因：

- 使用者隨時可能開新視窗、關視窗、移動視窗，如果快取住上一次算出來的
  落腳位置，只會讓桌寵卡在一個已經不存在（或已經移動過）的平台上。
- 這個「每 tick 都重算」的副作用，剛好也是我們想要的行為：假設桌寵
  站著的那個視窗被關掉或移走了，下一個 tick 重新計算時就會找不到那個
  平台，桌寵自然就會開始往下一個平台掉落——不需要另外寫一段
  「偵測平台消失」的特殊邏輯，重算本身就順便處理掉了這個情境。
"""

import ctypes
from ctypes import wintypes
from typing import Optional

import win32gui

# Windows API：SystemParametersInfo 用來查詢「可用桌面工作區」的參數代碼。
_SPI_GETWORKAREA = 0x0030

# 太窄的視窗（例如工具列上的小面板）不當作落腳點，
# 避免桌寵的立繪比落點還寬，看起來卡在邊緣很奇怪。
_MIN_SURFACE_WIDTH = 60


def work_area_bottom() -> int:
    """回傳工作列上緣的 y 座標（也就是可用桌面高度的下緣）。

    這個值當作「最終地板」使用——就算找不到任何其他視窗可以站，
    也一定會有這個當作最底層的落腳點，桌寵不會無止盡往下掉出螢幕。
    """
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(_SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return rect.bottom


def _visible_window_rects(exclude_hwnd: Optional[int]) -> list[tuple[int, int, int, int]]:
    """列出目前所有「看起來像正常應用程式視窗」的可見視窗範圍，並排除桌寵自己。

    這裡篩選落點的標準抓得比較寬鬆也沒關係——跟 screen_watch.py 的黑名單
    邏輯不一樣，站錯地方最多只是懸空感覺有點怪，不像螢幕讀取功能那樣
    牽涉到隱私疑慮。因此只濾掉最小化、沒有標題、太窄這三種明顯不適合
    當落腳點的視窗。
    """
    rects: list[tuple[int, int, int, int]] = []

    def _callback(hwnd: int, _extra) -> bool:
        # 排除桌寵自己的視窗，不然桌寵會把自己當成可以站的平台。
        if hwnd == exclude_hwnd:
            return True
        # 不可見或已最小化的視窗，不應該被當成落腳點。
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return True
        # 沒有標題的視窗通常是系統背景元件，不是使用者實際在看的視窗。
        if not win32gui.GetWindowText(hwnd):
            return True

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right - left < _MIN_SURFACE_WIDTH:
            return True

        rects.append((left, top, right, bottom))
        return True

    win32gui.EnumWindows(_callback, None)
    return rects


def find_landing_y(
    char_left: int,
    char_right: int,
    char_bottom: int,
    exclude_hwnd: Optional[int],
) -> int:
    """找出角色目前這個 x 範圍，正下方最近的落腳面 y 座標。

    回傳值代表「角色底部應該貼齊的 y 座標」。

    候選落點的篩選規則：
    - x 範圍要跟角色目前的左右範圍有重疊（否則不算「正下方」）。
    - 該視窗的頂邊要在角色目前底部之下（或非常接近），
      如果頂邊已經在角色上方，代表角色其實卡在那個視窗裡面了，
      這種情況不應該被當成落點。
    - 在所有符合條件的候選裡，取「最靠近角色底部」的那一個
      （也就是 y 座標最小的那個，因為螢幕座標系 y 值越小代表越靠上）。

    如果完全沒有符合條件的視窗可以當落點，就回退到工作列上緣
    （也就是 work_area_bottom() 這個最終地板）。
    """
    best = work_area_bottom()

    for left, top, right, _bottom in _visible_window_rects(exclude_hwnd):
        if not (left < char_right and right > char_left):
            # x 範圍沒有重疊，這個視窗不在角色正下方，跳過。
            continue
        if top < char_bottom - 4:
            # 頂邊已經在角色目前底部的上方，代表角色其實卡在裡面了，
            # 不應該把這個視窗當成落點。
            continue
        if top < best:
            best = top

    return best
