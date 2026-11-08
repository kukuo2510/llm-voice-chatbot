"""桌寵「殼」：跟角色人設／對話／配音完全無關的共用邏輯。

存在的目的是讓 desktop_pet.py（範例角色 Sakiko，深度對話型）跟之後的其他角色
可以共用同一套：
- 視窗去背
- 立繪載入／縮放
- 螢幕擷取排除

這些邏輯，不用整份複製貼上到每一個角色的桌寵腳本裡。

## 搬移備註

原本這幾個函式都定義在 desktop_pet.py 裡面，並且直接讀取模組層級的
ASSETS_DIR 全域常數。搬來這裡的時候，全部改成用參數傳入素材路徑，
邏輯完全照搬、沒有改變任何行為，目的是讓不同角色能各自傳入自己的
素材路徑，而不用共用同一個寫死的全域常數。
"""

from pathlib import Path
from typing import Optional

from PIL import Image, ImageTk

# 視窗去背用的色鍵顏色，選一個角色圖裡不會出現的顏色（洋紅色），
# 才能確保色鍵去背不會不小心把角色圖本身的某些顏色也挖空。
MAGIC_TRANSPARENT_COLOR = "#ff00ff"

# win32 GetAncestor 的旗標常數：2 代表「取得最上層視窗（root window）」。
_GA_ROOT = 2

# win32 SetWindowDisplayAffinity 的旗標常數：代表「把這個視窗排除在
# 螢幕擷取／截圖之外」。
_WDA_EXCLUDEFROMCAPTURE = 0x00000011


def exclude_self_from_screen_capture(tk_hwnd: int) -> int:
    """讓 screen_watch 之類的全螢幕擷取功能，自動跳過桌寵本體的視窗。

    背景：如果不排除的話，「看螢幕」功能會把桌寵自己的立繪也一起
    拍進擷取畫面裡，變成一種奇怪的「自拍」。

    實作細節：pywin32 沒有包裝 SetWindowDisplayAffinity 這個 API，
    所以這裡直接用 ctypes 呼叫 user32.dll。如果呼叫失敗（例如在
    不支援這個 API 的舊版 Windows 上執行），就當作沒發生過這件事，
    不影響桌寵其他功能正常運作。

    回傳值是解析出來的「最上層視窗」hwnd（如果解析失敗，就回傳
    原本傳進來的 tk_hwnd）。呼叫端可以重複使用這個回傳的 hwnd
    去做其他 win32 相關呼叫（例如 desktop_physics 的落地判定，
    也需要用同一個 hwnd 把桌寵自己從候選落點清單中排除）。
    """
    import ctypes

    try:
        user32 = ctypes.windll.user32
        root_hwnd = user32.GetAncestor(tk_hwnd, _GA_ROOT) or tk_hwnd
        user32.SetWindowDisplayAffinity(root_hwnd, _WDA_EXCLUDEFROMCAPTURE)
        return root_hwnd
    except Exception:
        return tk_hwnd


# 立繪素材快取：key 是 assets_dir，讓不同角色的立繪各自快取、互不覆蓋，
# 這樣同一支程式裡如果同時管理多個角色的素材，也不會互相干擾。
_avatar_source_cache: dict[Path, tuple] = {}


def load_avatar_source(assets_dir: Path) -> tuple:
    """讀取 assets_dir 底下兩張完整的角色立繪，並算出共用的裁切框。

    讀取的兩張圖分別是 mouth_closed.png（閉嘴）跟 mouth_open.png
    （開嘴），這兩張各自是完整的角色圖，不是用來疊圖合成的局部素材。

    同一個 assets_dir 只會真正執行一次讀取＋計算，結果會被快取起來。
    這個限制是刻意的：這個步驟包含磁碟讀取，以及在原始解析度上
    計算透明像素範圍，相對比較花時間，但這個計算結果完全跟「之後
    要縮放到多寬」無關——如果每次滾輪縮放都重新做一次這個步驟，
    純粹是浪費運算資源。
    """
    if assets_dir in _avatar_source_cache:
        return _avatar_source_cache[assets_dir]

    closed = Image.open(assets_dir / "mouth_closed.png").convert("RGBA")
    opened = Image.open(assets_dir / "mouth_open.png").convert("RGBA")

    def _content_bbox(img: Image.Image):
        """算出圖片裡「非透明內容」所佔的最小外框（bounding box）。"""
        return img.split()[-1].point(lambda a: 255 if a > 200 else 0).getbbox()

    # 這兩張圖是各自獨立生成的，不透明範圍可能會有些微差異
    # （例如頭髮的姿態不會完全一致）。這裡用「兩張圖的聯集」
    # 算出同一個裁切框，讓兩張圖都套用相同的裁切範圍——
    # 這樣在嘴型開闔切換的動畫裡，才不會因為裁切位置不同
    # 而讓角色看起來突然跳動一下。
    cx0, cy0, cx1, cy1 = _content_bbox(closed)
    ox0, oy0, ox1, oy1 = _content_bbox(opened)
    x0, y0 = min(cx0, ox0), min(cy0, oy0)
    x1, y1 = max(cx1, ox1), max(cy1, oy1)

    # 左右各留一點邊界（內容寬度的 15%），避免裁切線剛好貼齊
    # 角色輪廓邊緣，看起來太緊繃。
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

    嘴巴的位置／大小完全由原始素材圖本身決定，這支函式不會再對嘴巴
    區域做額外的自動裁切或疊圖處理。

    mirror=True 時會把圖片左右翻轉——這是給桌寵漫遊「往左走」時使用，
    這樣就不用另外準備一組「面向左」的立繪素材，省下美術成本。

    回傳值是 (closed_photo, open_photo, display_height) 這個 tuple。
    注意：PhotoImage 物件是 Tkinter 的特殊物件，呼叫端一定要自己留住
    這個回傳值的參照，不然一旦被 Python 的垃圾回收機制（GC）回收掉，
    畫面上顯示的立繪就會突然變成空白。
    """
    closed_cropped, opened_cropped, aspect_ratio = load_avatar_source(assets_dir)

    if mirror:
        closed_cropped = closed_cropped.transpose(Image.FLIP_LEFT_RIGHT)
        opened_cropped = opened_cropped.transpose(Image.FLIP_LEFT_RIGHT)

    display_height = round(display_width * aspect_ratio)

    def _to_photo(img: Image.Image) -> ImageTk.PhotoImage:
        resized = img.resize((display_width, display_height), Image.LANCZOS)

        # 色鍵去背只能做到「整個像素全透明」或「整個像素全不透明」，
        # 沒辦法處理漸層的 alpha 值。所以這裡要先把 alpha 值二值化
        # （大於 128 視為完全不透明、否則視為完全透明），不然邊緣
        # 半透明的像素會在去背後留下一圈色鍵顏色的毛邊。
        # 這樣做的代價是邊緣會變成硬邊，沒有平滑的抗鋸齒效果。
        alpha = resized.split()[-1].point(lambda a: 255 if a > 128 else 0)

        canvas = Image.new("RGB", resized.size, MAGIC_TRANSPARENT_COLOR)
        canvas.paste(resized.convert("RGB"), mask=alpha)
        return ImageTk.PhotoImage(canvas)

    return _to_photo(closed_cropped), _to_photo(opened_cropped), display_height
