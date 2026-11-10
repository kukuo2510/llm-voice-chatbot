"""對話氣泡的純幾何：斷行、氣泡大小、相對頭頂的位置、限制在螢幕工作區內。

這裡只有純函式（不碰 tkinter／PIL／win32），測試可以直接呼叫。
座標一律是螢幕像素，矩形用 (left, top, right, bottom)。
"""

BUBBLE_MAX_TEXT_WIDTH = 260  # 文字換行寬度上限（像素）
BUBBLE_PAD_X = 14
BUBBLE_PAD_Y = 10
BUBBLE_RADIUS = 14
BUBBLE_TAIL_H = 14  # 氣泡下方小尾巴的高度
BUBBLE_TAIL_HALF_W = 9
BUBBLE_GAP = 4  # 尾巴尖端跟頭頂之間留的空隙
SCREEN_MARGIN = 4


def _units(line: str) -> list[str]:
    """把一行拆成可斷行的單位：連續的英數字是一個單位、空白獨立、其他字（中日文等）一字一個。"""
    units: list[str] = []
    buf = ""
    for ch in line:
        if ch.isascii() and not ch.isspace():
            buf += ch
            continue
        if buf:
            units.append(buf)
            buf = ""
        units.append(ch)
    if buf:
        units.append(buf)
    return units


def wrap_text(text: str, measure, max_width: int) -> list[str]:
    """依 measure(字串)->像素寬 把文字斷成多行，每行不超過 max_width（單一字元太寬時例外）。"""
    lines: list[str] = []
    for raw in text.split("\n"):
        cur = ""
        for unit in _units(raw):
            if unit.isspace() and not cur:
                continue  # 行首的空白不留
            if measure(cur + unit) <= max_width:
                cur += unit
                continue
            if cur:
                lines.append(cur.rstrip())
                cur = ""
            if unit.isspace():
                continue
            # 超長的英數單字：照字元硬切。
            while measure(unit) > max_width and len(unit) > 1:
                k = len(unit) - 1
                while k > 1 and measure(unit[:k]) > max_width:
                    k -= 1
                lines.append(unit[:k])
                unit = unit[k:]
            cur = unit
        lines.append(cur.rstrip())
    return lines or [""]


def bubble_size(text_w: int, text_h: int, tail: bool = True) -> tuple[int, int]:
    """文字區塊大小 -> 整個氣泡圖（含尾巴空間）的大小。"""
    w = text_w + 2 * BUBBLE_PAD_X
    h = text_h + 2 * BUBBLE_PAD_Y + (BUBBLE_TAIL_H if tail else 0)
    return w, h


def head_point(win_x: int, win_y: int, win_w: int, win_h: int, cx_frac: float, top_frac: float) -> tuple[int, int]:
    """頭頂的螢幕座標。cx_frac／top_frac 是頭頂在立繪視窗內的比例位置（由透明度外框算出，不寫死）。"""
    return round(win_x + cx_frac * win_w), round(win_y + top_frac * win_h)


def head_fractions(bbox: tuple[int, int, int, int], img_w: int, img_h: int) -> tuple[float, float]:
    """從圖片的非透明外框 (x0,y0,x1,y1) 算出頭頂的水平中心比例與頂端比例。"""
    x0, y0, x1, _ = bbox
    return ((x0 + x1) / 2) / img_w, y0 / img_h


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def place_bubble(
    head: tuple[int, int],
    size: tuple[int, int],
    win_rect: tuple[int, int, int, int],
    work_area: tuple[int, int, int, int],
) -> dict:
    """決定氣泡位置。

    size 是整個氣泡圖（含尾巴空間）。優先放在頭頂上方、尾巴指著頭；上方放不下就放在視窗旁邊
    （右邊放得下放右邊，否則左邊），旁邊不畫尾巴。最後把整個氣泡夾在工作區內。
    回傳 {"x","y","side","tail_dx"}；tail_dx 是尾巴尖端相對氣泡左緣的 x（side != "above" 時為 None）。
    """
    hx, hy = head
    bw, bh = size
    wl, wt, wr, wb = work_area
    ml, mt, mr, mb = wl + SCREEN_MARGIN, wt + SCREEN_MARGIN, wr - SCREEN_MARGIN, wb - SCREEN_MARGIN
    bw = min(bw, mr - ml)
    bh = min(bh, mb - mt)

    top = hy - BUBBLE_GAP - bh
    if top >= mt:
        x = clamp(hx - bw / 2, ml, mr - bw)
        tail_min = BUBBLE_RADIUS + BUBBLE_TAIL_HALF_W
        tail_dx = clamp(hx - x, tail_min, max(tail_min, bw - tail_min))
        return {"x": round(x), "y": round(top), "side": "above", "tail_dx": round(tail_dx)}

    wx0, wy0, wx1, wy1 = win_rect
    if wx1 + BUBBLE_GAP + bw <= mr:
        x, side = wx1 + BUBBLE_GAP, "right"
    elif wx0 - BUBBLE_GAP - bw >= ml:
        x, side = wx0 - BUBBLE_GAP - bw, "left"
    else:
        x, side = clamp(hx - bw / 2, ml, mr - bw), "right"
    y = clamp(hy, mt, mb - bh)
    return {"x": round(x), "y": round(y), "side": side, "tail_dx": None}
