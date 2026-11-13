"""時段判斷（純邏輯，不碰 Tk；現在時間由外面傳入，方便測試）。

animations.json 的 "hours": [開始時, 結束時]：本機時間的整點，開始含、結束不含，可跨午夜。
    [9, 17]  -> 9:00 到 16:59
    [23, 7]  -> 23:00 到隔天 6:59
    [0, 24]  -> 全天（結束時允許 24）
開始＝結束視為不合理（不知道是空還是全天），解析時丟 ValueError。
"""

from datetime import datetime


def parse_hours(value):
    """解析 hours：None 回傳 None（不限時段）；合法回傳 (start, end) 整數；其餘丟 ValueError。"""
    if value is None:
        return None
    try:
        start, end = value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"hours 必須是 [開始時, 結束時]：{value!r}") from exc
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
        raise ValueError(f"hours 必須是整數：{value!r}")
    if not (0 <= start <= 23 and 0 <= end <= 24) or start == end:
        raise ValueError(f"hours 不合理：{value!r}")
    return start, end


def in_hours(hours, now: datetime = None) -> bool:
    """now（預設現在的本機時間）是否落在 hours 範圍內；hours 為 None 一律回傳 True。"""
    if hours is None:
        return True
    hour = (now or datetime.now()).hour
    start, end = hours
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # 跨午夜
