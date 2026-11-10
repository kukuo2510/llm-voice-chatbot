"""程式產生的身體動作（搖擺、上提、壓扁、晃動），疊在逐格動畫上面。

有些逐格素材的走路／掉落／拖曳／落地圖本身幾乎不動（走路只有腳小幅變化、拖曳是一張靜態側身），
只換圖看起來像「轉個身在滑」。這裡依「目前狀態」和「進入狀態後經過的秒數」算出一組變形參數，
再把那一幀做旋轉／縮放。

純函式、不碰 Tk：fx_for() 算參數，apply_fx() 對 PIL 圖做變形，方便單元測試。
"""

import math
from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class Fx:
    angle: float = 0.0  # 度，正值逆時針
    sx: float = 1.0  # 水平縮放
    sy: float = 1.0  # 垂直縮放
    pivot: str = "bottom"  # 變形的支點：bottom（腳底）或 top（被拎著的頭頂）

    @property
    def is_identity(self) -> bool:
        return self.angle == 0 and self.sx == 1 and self.sy == 1


IDENTITY = Fx()

WALK_STEPS_PER_SEC = 2.2  # 每秒踏幾步（左右各算一步）
WALK_SWAY_DEG = 3.0  # 走路左右搖擺角度
WALK_LIFT = 0.035  # 每一步身體上提的比例（用壓扁回彈表現）
DRAG_SWAY_DEG = 7.0  # 被拎著時的擺盪角度
DRAG_SWAY_HZ = 1.1
LAND_SQUASH = 0.16  # 落地瞬間壓扁的比例
POKE_SHAKE_DEG = 4.0


def _quant(value: float, step: float) -> float:
    """量化參數，讓變形後的圖可以快取重用（不會每個 tick 都算一張新的）。"""
    return round(value / step) * step


def fx_for(key: str, t: float) -> Fx:
    """依狀態與進入狀態後經過的秒數 t，回傳這一刻的變形。沒有特效的狀態回傳 IDENTITY。"""
    t = max(0.0, t)
    if key == "walk":
        phase = math.pi * WALK_STEPS_PER_SEC * t  # 每半個週期一步
        angle = WALK_SWAY_DEG * math.sin(phase)
        # 每一步中段身體上提：視窗高度固定，用「先壓低、再回彈」表現步伐的起伏
        sy = 1.0 - WALK_LIFT * (1.0 - abs(math.sin(phase)))
        return Fx(_quant(angle, 0.5), 1.0, _quant(sy, 0.005))
    if key == "drag":
        angle = DRAG_SWAY_DEG * math.sin(2 * math.pi * DRAG_SWAY_HZ * t)
        return Fx(_quant(angle, 0.5), 1.0, 1.0, "top")
    if key == "fall":
        return Fx(0.0, 0.95, 1.0)  # 往下掉時身體縮窄一點，像被往下拉長
    if key == "land":
        k = math.exp(-t * 9.0) * math.cos(t * 22.0)  # 壓扁後彈兩下
        sy = 1.0 - LAND_SQUASH * k
        sx = 1.0 + LAND_SQUASH * 0.5 * k
        if abs(sy - 1.0) < 0.004:
            return IDENTITY
        return Fx(0.0, _quant(min(sx, 1.08), 0.005), _quant(sy, 0.005))
    if key == "poke":
        angle = POKE_SHAKE_DEG * math.sin(t * 38.0) * math.exp(-t * 6.0)
        if abs(angle) < 0.25:
            return IDENTITY
        return Fx(_quant(angle, 0.5))
    return IDENTITY


def apply_fx(img: Image.Image, fx: Fx) -> Image.Image:
    """對一張 RGBA 幀套用變形，輸出同尺寸（視窗大小固定，超出邊界的部分裁掉）。"""
    if fx.is_identity:
        return img
    w, h = img.size
    out = img
    if fx.sx != 1 or fx.sy != 1:
        nw, nh = max(1, round(w * fx.sx)), max(1, round(h * fx.sy))
        scaled = img.resize((nw, nh), Image.LANCZOS)
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        x = (w - nw) // 2
        y = 0 if fx.pivot == "top" else h - nh  # 腳底（或頭頂）固定不動
        out.paste(scaled, (x, y))
    if fx.angle:
        center = (w / 2, 0) if fx.pivot == "top" else (w / 2, h)
        out = out.rotate(fx.angle, resample=Image.BICUBIC, center=center)
    return out
