"""立繪的「可見內容外框」：頭頂位置（氣泡用）、腳底基準線與身高（動畫對齊用）。

一律從圖片非透明像素算出來，不寫死某一張圖。座標是「裁切後立繪圖」的像素，
要換成螢幕上的位置時乘上 顯示寬度 / 裁切後寬度 即可（見 pet_bubble_window）。
"""

from pathlib import Path

from pet_bubble import head_fractions

_cache: dict[Path, dict] = {}


def content_bbox(img) -> tuple[int, int, int, int]:
    """非透明內容的最小外框（alpha > 200，跟 pet_shell 裁切用的判定一致）。"""
    box = img.split()[-1].point(lambda a: 255 if a > 200 else 0).getbbox()
    return box if box else (0, 0, img.width, img.height)


def avatar_metrics(assets_dir: Path) -> dict:
    """回傳 {"crop_w","crop_h","bbox","cx_frac","top_frac"}。讀不到素材時丟例外，由呼叫端決定怎麼退回。"""
    if assets_dir in _cache:
        return _cache[assets_dir]
    from pet_shell import load_avatar_source

    closed, _opened, _aspect = load_avatar_source(assets_dir)
    bbox = content_bbox(closed)
    cx_frac, top_frac = head_fractions(bbox, closed.width, closed.height)
    result = {
        "crop_w": closed.width,
        "crop_h": closed.height,
        "bbox": bbox,
        "cx_frac": cx_frac,
        "top_frac": top_frac,
    }
    _cache[assets_dir] = result
    return result
