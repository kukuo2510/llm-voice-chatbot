"""動畫狀態機（純邏輯，不碰 Tk；時間與亂數都從外面傳入，方便測試）。

狀態：idle（預設）／walk（物理真的在水平移動時）／某個 occasional 小動作（閒置 20～60 秒隨機演一次）。
說話中（talking）一律回傳 None，表示「動畫暫停，立繪交給嘴型換圖」，並取消進行中的小動作。
"""

import random


class AnimController:
    def __init__(self, config, rng=None):
        self.config = config
        self.rng = rng or random
        self.key = "idle"
        self._start = None
        self._end = None  # 小動作的結束時間
        self._next_occ = None

    def _schedule_next(self, now: float) -> None:
        lo, hi = self.config.interval
        self._next_occ = now + self.rng.uniform(lo, hi)

    def _enter(self, key: str, now: float) -> None:
        self.key = key
        self._start = now
        self._end = None

    def _set_for(self, key: str):
        return self.config.states.get(key) or self.config.occasional[key]

    def _pick_occasional(self) -> str:
        names = list(self.config.occasional)
        weights = [self.config.occasional[n].weight for n in names]
        return self.rng.choices(names, weights=weights, k=1)[0]

    def step(self, now: float, talking: bool, walking: bool, allow_occasional: bool = True):
        """推進狀態機，回傳 (key, frame_index)；說話中回傳 None。"""
        if self._start is None:
            self._enter("idle", now)
            self._schedule_next(now)

        if talking:
            if self.key != "idle":
                self._enter("idle", now)
            self._schedule_next(now)  # 講完話不要馬上跳小動作
            self._start = None  # 下次 step 重新開始 idle
            return None

        in_occ = self.key in self.config.occasional
        if walking and "walk" in self.config.states:
            if self.key != "walk":
                self._enter("walk", now)
                self._schedule_next(now)
        elif in_occ:
            if walking or now >= self._end:
                self._enter("idle", now)
                self._schedule_next(now)
        else:
            if self.key == "walk":
                self._enter("idle", now)
                self._schedule_next(now)
            if (self.key == "idle" and allow_occasional and self.config.occasional
                    and now >= self._next_occ):
                name = self._pick_occasional()
                self._enter(name, now)
                aset = self.config.occasional[name]
                if aset.loop:
                    self._end = now + self.rng.uniform(*aset.duration)
                else:
                    self._end = now + len(aset.frames) / aset.fps

        return self.key, self.frame_index(now)

    def frame_index(self, now: float) -> int:
        aset = self._set_for(self.key)
        idx = int((now - self._start) * aset.fps)
        n = len(aset.frames)
        return idx % n if aset.loop else min(idx, n - 1)
