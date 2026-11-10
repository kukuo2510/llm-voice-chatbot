"""把氣泡／輸入列的外型用 PIL 畫成圖片（洋紅色底，配合視窗的色鍵去背，只剩氣泡形狀）。

文字也在這裡畫（用 PIL 字型量測，才能先算出氣泡大小）。邊緣用 4 倍超取樣再二值化，
避免色鍵去背留下洋紅色毛邊。
"""

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from pet_bubble import (
    BUBBLE_MAX_TEXT_WIDTH,
    BUBBLE_PAD_X,
    BUBBLE_PAD_Y,
    BUBBLE_RADIUS,
    BUBBLE_TAIL_H,
    BUBBLE_TAIL_HALF_W,
    bubble_size,
    wrap_text,
)
from pet_config import (BUBBLE_BORDER, BUBBLE_FILL, BUBBLE_STATUS_BORDER, BUBBLE_STATUS_FILL,
                        BUBBLE_STATUS_TEXT, BUBBLE_TEXT)
from pet_shell import MAGIC_TRANSPARENT_COLOR

FONT_PX = 16
LINE_GAP = 4
_SS = 4  # 超取樣倍率
_FONT_CANDIDATES = ("msjh.ttc", "msjh.ttf", "NotoSansCJK-Regular.ttc", "arial.ttf")

# 一般台詞：面板色底＋紫色邊；狀態文字：更暗的底與邊、文字用較暗的顏色。
_STYLE_NORMAL = {"fill": BUBBLE_FILL, "border": BUBBLE_BORDER, "text": BUBBLE_TEXT}
_STYLE_STATUS = {"fill": BUBBLE_STATUS_FILL, "border": BUBBLE_STATUS_BORDER, "text": BUBBLE_STATUS_TEXT}


@lru_cache(maxsize=1)
def get_font() -> ImageFont.ImageFont:
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, FONT_PX)
        except OSError:
            continue
    return ImageFont.load_default()


def style_for(is_status: bool) -> dict:
    return _STYLE_STATUS if is_status else _STYLE_NORMAL


def layout_text(text: str) -> tuple[list[str], int, int]:
    """斷行並量出文字區塊大小 -> (lines, text_w, text_h)。"""
    font = get_font()
    lines = wrap_text(text, font.getlength, BUBBLE_MAX_TEXT_WIDTH)
    text_w = max(1, round(max(font.getlength(ln) for ln in lines)))
    text_h = len(lines) * FONT_PX + (len(lines) - 1) * LINE_GAP
    return lines, text_w, text_h


def draw_shape(w: int, h: int, tail_dx, fill: str, border: str, border_px: int = 2) -> Image.Image:
    """畫圓角矩形（有 tail_dx 時下方帶一個指向下的小尾巴），回傳 RGB 圖，外面是色鍵洋紅色。"""
    body_h = h - BUBBLE_TAIL_H  # 尾巴的空間一律保留，沒有尾巴時圖大小不變

    def _mask(inset: int) -> Image.Image:
        m = Image.new("L", (w * _SS, h * _SS), 0)
        d = ImageDraw.Draw(m)
        box = [inset * _SS, inset * _SS, (w - 1 - inset) * _SS, (body_h - 1 - inset) * _SS]
        d.rounded_rectangle(box, radius=max(1, (BUBBLE_RADIUS - inset)) * _SS, fill=255)
        if tail_dx is not None:
            tx = tail_dx
            y0 = body_h - 1 - inset - 2
            pts = [
                ((tx - BUBBLE_TAIL_HALF_W + inset) * _SS, y0 * _SS),
                ((tx + BUBBLE_TAIL_HALF_W - inset) * _SS, y0 * _SS),
                (tx * _SS, (h - 1 - inset) * _SS),
            ]
            d.polygon(pts, fill=255)
        m = m.resize((w, h), Image.LANCZOS)
        return m.point(lambda a: 255 if a > 128 else 0)

    img = Image.new("RGB", (w, h), MAGIC_TRANSPARENT_COLOR)
    img.paste(Image.new("RGB", (w, h), border), mask=_mask(0))
    img.paste(Image.new("RGB", (w, h), fill), mask=_mask(border_px))
    return img


def render_bubble(text: str, is_status: bool, tail_dx) -> Image.Image:
    """畫出含文字的對話氣泡。tail_dx=None 表示不畫尾巴（放在旁邊時）。"""
    style = style_for(is_status)
    lines, tw, th = layout_text(text)
    w, h = bubble_size(tw, th, tail=True)
    img = draw_shape(w, h, tail_dx, style["fill"], style["border"])
    d = ImageDraw.Draw(img)
    font = get_font()
    y = BUBBLE_PAD_Y
    for ln in lines:
        d.text((BUBBLE_PAD_X, y - 2), ln, font=font, fill=style["text"])
        y += FONT_PX + LINE_GAP
    return img


def bubble_image_size(text: str) -> tuple[int, int]:
    """不真的畫圖，只算氣泡圖的大小（含尾巴空間），給定位用。"""
    _, tw, th = layout_text(text)
    return bubble_size(tw, th, tail=True)


def render_input_frame(w: int, h: int, tail_dx) -> Image.Image:
    """輸入列的外框（圓角＋尾巴），文字輸入框之後疊在上面。w,h 含尾巴空間。"""
    style = _STYLE_NORMAL
    return draw_shape(w, h, tail_dx, style["fill"], style["border"])
