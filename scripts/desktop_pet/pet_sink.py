"""支撐線下沉量（純邏輯）：phase 的 anchor_y（圖中支撐線在「內容高度」的比例）換成視窗要往下沉幾像素。

例：坐在邊緣的圖，屁股在內容高度 69% 處、腳垂在下面。物理要讓「支撐線」對齊落腳面，
所以視窗底部要低於落腳面 (1 - anchor_y) * 內容高度。
內容高度＝這組「已正規化的幀」的非透明外框聯集高度（幀的透明邊不算），
也就是畫面上真的看得到的身高（高度已含 height_ratio 與縮放）。
"""

from pet_anim_frames import union_bbox


def content_height(frames) -> int:
    """一組已正規化幀（RGBA）實際顯示的內容高度（像素）。"""
    box = union_bbox(frames)
    return max(0, box[3] - box[1])


def sink_px(anchor_y, height: float) -> int:
    """視窗要往下沉的像素：anchor_y 為 None 或不在 0~1 之間回傳 0。"""
    if anchor_y is None or not 0 <= anchor_y <= 1:
        return 0
    return round((1 - anchor_y) * height)
