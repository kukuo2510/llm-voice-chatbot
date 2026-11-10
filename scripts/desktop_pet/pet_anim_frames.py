"""動畫幀的正規化：不同畫布大小的幀 -> 同樣身高、腳底同一條基準線、水平置中的整組圖。

做法（每一組動畫各自處理）：
1. 取這組所有幀的「非透明外框聯集」，每幀都用同一個外框裁切（組內的動作位移才會保留，不會抖）。
2. 縮放讓這組的可見身高 = target_h（= 靜態立繪的可見身高，所以各組、也跟說話時的立繪一樣大）。
3. 貼到跟立繪視窗同大小的透明畫布上，外框底邊對齊 baseline_y、水平中心對齊 center_x。
純 PIL，不碰 Tk，測試可直接呼叫。
"""

from PIL import Image

from pet_avatar_metrics import avatar_metrics, content_bbox
from pet_shell import MAGIC_TRANSPARENT_COLOR

_source_cache: dict = {}


def load_frame(path) -> Image.Image:
    """讀一張幀（RGBA），同一個路徑只讀一次。"""
    img = _source_cache.get(path)
    if img is None:
        img = Image.open(path).convert("RGBA")
        _source_cache[path] = img
    return img


def union_bbox(images) -> tuple:
    boxes = [content_bbox(im) for im in images]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def normalize_set(images, target_h: int, canvas_size: tuple, center_x: float, baseline_y: float,
                  ub=None) -> list:
    """把一組幀正規化，回傳等長的 RGBA 畫布清單（見模組說明）。ub：指定外框（多段共用縮放基準時）。"""
    ub = ub or union_bbox(images)
    src_h = max(1, ub[3] - ub[1])
    scale = target_h / src_h
    # 太寬（例如帶道具）就整組縮到放得進畫布，不要被切掉
    scale = min(scale, canvas_size[0] / max(1, ub[2] - ub[0]))
    out_w = max(1, round((ub[2] - ub[0]) * scale))
    out_h = max(1, round(src_h * scale))
    left = min(max(0, round(center_x - out_w / 2)), max(0, canvas_size[0] - out_w))  # 不超出左右邊
    top = round(baseline_y) - out_h
    frames = []
    for im in images:
        piece = im.crop(ub).resize((out_w, out_h), Image.LANCZOS)
        canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        canvas.alpha_composite(piece, dest=(max(0, left), max(0, top)),
                               source=(max(0, -left), max(0, -top)))
        frames.append(canvas)
    return frames


def mirror_all(images) -> list:
    return [im.transpose(Image.FLIP_LEFT_RIGHT) for im in images]


def colorkey_rgb(img: Image.Image) -> Image.Image:
    """跟 pet_shell.load_avatar_frames 一樣的去背：alpha 二值化後貼到洋紅色底。"""
    alpha = img.split()[-1].point(lambda a: 255 if a > 128 else 0)
    canvas = Image.new("RGB", img.size, MAGIC_TRANSPARENT_COLOR)
    canvas.paste(img.convert("RGB"), mask=alpha)
    return canvas


def target_geometry(assets_dir, display_w: int) -> tuple:
    """靜態立繪在 display_w 寬時的 (畫布大小, 可見身高, 水平中心, 腳底基準線)。"""
    m = avatar_metrics(assets_dir)
    s = display_w / m["crop_w"]
    x0, y0, x1, y1 = m["bbox"]
    canvas = (display_w, round(m["crop_h"] * s))
    return canvas, (y1 - y0) * s, (x0 + x1) / 2 * s, y1 * s


def wide_canvas(images, target_h: int, canvas: tuple, center_x: float, ub=None) -> tuple:
    """寬場景用：畫布加寬到放得下整組（同樣的角色比例），回傳 (新畫布大小, 新水平中心)。

    加寬時以原本的水平中心為準往兩邊撐開，視窗端（pet_anim_window）也以中心為準加寬，角色不會跳位。
    """
    ub = ub or union_bbox(images)
    need_w = round((ub[2] - ub[0]) * target_h / max(1, ub[3] - ub[1]))
    if need_w <= canvas[0]:
        return canvas, center_x
    extra = need_w - canvas[0]
    extra += extra % 2  # 兩邊各撐一半，保持偶數
    return (canvas[0] + extra, canvas[1]), center_x + extra / 2


def build_set(anim_set, assets_dir, display_w: int, mirrored: bool) -> list:
    """載入並正規化一組動畫（RGBA，尚未去背）。"""
    canvas, height, cx, base = target_geometry(assets_dir, display_w)
    ratio = getattr(anim_set, "height_ratio", 1.0)
    images = [load_frame(p) for p in anim_set.frames]
    norm = getattr(anim_set, "norm_frames", ())
    ub = union_bbox([load_frame(p) for p in norm]) if norm else None
    if getattr(anim_set, "wide", False):
        canvas, cx = wide_canvas(images, round(height * ratio), canvas, cx, ub)
    frames = normalize_set(images, round(height * ratio), canvas, cx, base, ub)
    return mirror_all(frames) if mirrored else frames


def needs_mirror(native_facing: str, facing: str) -> bool:
    """圖本身面向 native_facing，現在要面向 facing：不同就左右翻轉；front 永不翻。"""
    return native_facing in ("left", "right") and native_facing != facing
