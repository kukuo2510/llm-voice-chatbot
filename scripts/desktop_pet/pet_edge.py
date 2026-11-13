"""邊緣坐下的走位計算（純邏輯，不碰 Tk）。

placement: "edge" 的多段動作抽中後，角色先走到目前支撐物（視窗頂邊或工作區地面）最近的左／右端，
面向外側，到了才開始演。這裡只算「目標 x」與「這一步走到哪」，實際移動在 pet_physics。
"""

EDGE_ARRIVE_PX = 2  # 離目標這麼近就算到了
EDGE_INSET_PX = 0  # 視窗中心離邊緣往內縮多少（0＝中心對齊邊緣，屁股剛好在邊上）


def plan_edge(x: float, w: float, left: float, right: float, screen_w: float):
    """回傳 (視窗目標 x, 到了之後的面向)。

    以視窗中心離哪一端比較近決定去哪邊；左端面向 "left"、右端面向 "right"（都是外側）。
    目標 x 鉗在螢幕內（支撐物是螢幕工作區時，視窗不會超出螢幕）。
    """
    center = x + w / 2
    if abs(center - left) <= abs(right - center):
        target_center, facing = left + EDGE_INSET_PX, "left"
    else:
        target_center, facing = right - EDGE_INSET_PX, "right"
    target = max(0, min(target_center - w / 2, screen_w - w))
    return target, facing


def step_toward(x: float, target: float, step: float):
    """從 x 朝 target 走最多 step 像素，回傳 (新 x, 是否已到達)。"""
    if abs(target - x) <= max(step, EDGE_ARRIVE_PX):
        return target, True
    return x + (step if target > x else -step), False
