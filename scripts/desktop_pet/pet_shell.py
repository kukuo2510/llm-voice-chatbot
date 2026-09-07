"""桌寵「殼」：跟角色人設/對話/配音完全無關的共用邏輯，讓
desktop_pet.py（Sakiko，深度對話型）跟其他角色（例如互動反應型的
desktop_pet_reactive.py）可以共用同一套視窗去背、立繪載入/縮放、螢幕擷取
排除邏輯，不用整份複製貼上。

原本這幾個函式都定義在 desktop_pet.py 裡、吃模組層級的 ASSETS_DIR 全域常數；
搬來這裡時全部改成吃參數，邏輯完全照搬、不改行為，讓不同角色能各自傳自己的
素材路徑進來。
"""
from pathlib import Path
from typing import Optional

from PIL import Image, ImageTk

MAGIC_TRANSPARENT_COLOR = "#ff00ff"  # 視窗去背色鍵，角色圖裡不會出現這個顏色

_GA_ROOT = 2  # win32 GetAncestor 旗標：取得最上層視窗
_WDA_EXCLUDEFROMCAPTURE = 0x00000011  # win32 SetWindowDisplayAffinity 旗標


def exclude_self_from_screen_capture(tk_hwnd: int) -> int:
    """讓 screen_watch 之類的全螢幕擷取跳過桌寵本體視窗，不然看螢幕功能會把
    自己的立繪也拍進去。pywin32 沒包這個 API，直接用 ctypes 呼叫 user32；失敗
    （例如舊版 Windows 不支援）就當作沒這回事，不影響其他功能。回傳解析出來的
    最上層 hwnd（失敗就回傳原本的 tk_hwnd），讓呼叫端可以重用這個 hwnd 做其他
    win32 呼叫（例如 desktop_physics 落地判定要排除自己）。"""
    import ctypes
    try:
        user32 = ctypes.windll.user32
        root_hwnd = user32.GetAncestor(tk_hwnd, _GA_ROOT) or tk_hwnd
        user32.SetWindowDisplayAffinity(root_hwnd, _WDA_EXCLUDEFROMCAPTURE)
        return root_hwnd
    except Exception:
        return tk_hwnd


# 立繪素材快取：key 是 assets_dir，讓不同角色的立繪各自快取，不會互相覆蓋
_avatar_source_cache: dict[Path, tuple] = {}


def load_avatar_source(assets_dir: Path) -> tuple:
    """讀 assets_dir 底下兩張完整的角色立繪（mouth_closed.png / mouth_open.png，
    各自是完整的角色圖，不是疊圖用的局部素材）、算裁切框，同一個 assets_dir 只
    做一次並快取——這步驟含磁碟讀取＋在原始解析度上算透明範圍，比較花時間，但
    跟縮放到多寬完全無關，滾輪縮放時重複做這步只是浪費。"""
    if assets_dir in _avatar_source_cache:
        return _avatar_source_cache[assets_dir]

    closed = Image.open(assets_dir / "mouth_closed.png").convert("RGBA")
    opened = Image.open(assets_dir / "mouth_open.png").convert("RGBA")

    def _content_bbox(img: Image.Image):
        return img.split()[-1].point(lambda a: 255 if a > 200 else 0).getbbox()

    # 兩張圖各自生成，不透明範圍可能有些微差異（頭髮姿態不會完全一致），這裡
    # 用兩張的聯集算同一個裁切框，兩張圖套用同一個框，避免切換嘴型時因為裁切
    # 位置不同讓角色看起來跳動
    cx0, cy0, cx1, cy1 = _content_bbox(closed)
    ox0, oy0, ox1, oy1 = _content_bbox(opened)
    x0, y0 = min(cx0, ox0), min(cy0, oy0)
    x1, y1 = max(cx1, ox1), max(cy1, oy1)
    pad = int((x1 - x0) * 0.15)
    left = max(0, x0 - pad)
    right = min(closed.width, x1 + pad)
    box = (left, 0, right, closed.height)

    closed_cropped = closed.crop(box)
    opened_cropped = opened.crop(box)
    aspect_ratio = closed_cropped.height / closed_cropped.width
    result = (closed_cropped, opened_cropped, aspect_ratio)
    _avatar_source_cache[assets_dir] = result
    return result


def load_avatar_frames(assets_dir: Path, display_width: int, mirror: bool = False):
    """把已經裁切好的立繪（見 load_avatar_source）縮放成面板要顯示的寬度。
    嘴巴位置/大小完全由畫圖那邊決定，這裡不再自動裁切疊圖。mirror=True 時
    左右翻轉——漫遊往左走時用，省了另外準備一組「面向左」立繪素材的成本。
    回傳 (closed_photo, open_photo, display_height)——PhotoImage 物件呼叫端
    要留住參照，不然會被 GC 掉、畫面變空白。"""
    closed_cropped, opened_cropped, aspect_ratio = load_avatar_source(assets_dir)
    if mirror:
        closed_cropped = closed_cropped.transpose(Image.FLIP_LEFT_RIGHT)
        opened_cropped = opened_cropped.transpose(Image.FLIP_LEFT_RIGHT)
    display_height = round(display_width * aspect_ratio)

    def _to_photo(img: Image.Image) -> ImageTk.PhotoImage:
        resized = img.resize((display_width, display_height), Image.LANCZOS)
        # 色鍵去背只能整格全透明或全不透明，不吃漸層 alpha，先二值化才不會
        # 在邊緣留下一圈色鍵色的毛邊（代價是邊緣是硬邊，沒有平滑抗鋸齒）
        alpha = resized.split()[-1].point(lambda a: 255 if a > 128 else 0)
        canvas = Image.new("RGB", resized.size, MAGIC_TRANSPARENT_COLOR)
        canvas.paste(resized.convert("RGB"), mask=alpha)
        return ImageTk.PhotoImage(canvas)

    return _to_photo(closed_cropped), _to_photo(opened_cropped), display_height
