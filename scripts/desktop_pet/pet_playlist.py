"""音樂播放清單（純邏輯，不碰 Tk／音訊）：掃資料夾、洗牌、下一首。"""

import random
from pathlib import Path

# 只掃資料夾第一層（不遞迴）；副檔名不分大小寫。
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".ogg")


def scan_folder(folder) -> list:
    """資料夾第一層可播放的音樂檔路徑（字串，依檔名排序）；資料夾不存在或空的回傳 []。"""
    if not folder:
        return []
    p = Path(folder)
    try:
        if not p.is_dir():
            return []
        return sorted(str(f) for f in p.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTS)
    except OSError:
        return []


class Playlist:
    """隨機順序播放：每輪把所有歌洗牌一次，播完重新洗牌（新一輪第一首盡量不跟上一首相同）。"""

    def __init__(self, tracks, rng=None):
        self._tracks = list(tracks)
        self._rng = rng or random.Random()
        self._order: list = []
        self._last = None

    def __len__(self) -> int:
        return len(self._tracks)

    def _reshuffle(self) -> None:
        order = self._tracks[:]
        self._rng.shuffle(order)
        if len(order) > 1 and order[0] == self._last:
            order.append(order.pop(0))  # 把重複的移到最後
        self._order = order

    def next(self):
        """下一首的路徑；清單是空的回傳 None。"""
        if not self._tracks:
            return None
        if not self._order:
            self._reshuffle()
        self._last = self._order.pop(0)
        return self._last
