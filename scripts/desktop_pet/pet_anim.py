"""動畫狀態機（純邏輯，不碰 Tk；時間與亂數都從外面傳入，方便測試）。

狀態：idle（預設）／walk（物理真的在水平移動時）／某個 occasional 小動作（閒置 20～60 秒隨機演一次）。
說話中（talking）一律回傳 None，表示「動畫暫停，立繪交給嘴型換圖」，並取消進行中的小動作。

觸發式反應（config.reactions，選填）：dragging 演 drag、falling 演 fall（兩者持續到條件消失）；
poke(now)／land(now) 啟動一次性動作，演到結束時間才回 idle。
優先順序：talking > dragging > falling > 進行中的 poke/land > walking > 小動作/idle。
注意：反應的 key 就是 "poke"/"drag"/"fall"/"land"，小動作不要取同樣的名字。
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
        self._poke_last = None  # 上一次被接受的戳的時間
        self._poke_level = -1

    def _schedule_next(self, now: float) -> None:
        lo, hi = self.config.interval
        self._next_occ = now + self.rng.uniform(lo, hi)

    def _enter(self, key: str, now: float) -> None:
        self.key = key
        self._start = now
        self._end = None

    def _set_for(self, key: str):
        return (self.config.states.get(key) or self.config.reactions.get(key)
                or self.config.occasional[key])

    def _leave_to_idle(self, now: float) -> None:
        """結束反應／小動作回 idle，並重排下一次小動作（不要一結束馬上又演）。"""
        self._enter("idle", now)
        self._schedule_next(now)

    def poke(self, now: float):
        """戳臉：回傳升級等級 0/1/2…（眨眼／皺眉／鬧彆扭）；沒設定 poke 或在冷卻內回傳 None。"""
        if "poke" not in self.config.reactions:
            return None
        cfg = self.config
        if self._poke_last is not None and now - self._poke_last < cfg.poke_cooldown_sec:
            return None
        if self._poke_last is None or now - self._poke_last > cfg.poke_reset_sec:
            level = 0
        else:
            level = min(self._poke_level + 1, len(cfg.poke_sequence) - 1)  # 超過就停在最後一級
        self._poke_last, self._poke_level = now, level
        self._enter("poke", now)  # 會取消進行中的小動作
        self._end = now + cfg.poke_sequence[min(level, len(cfg.poke_sequence) - 1)]
        return level

    def land(self, now: float) -> None:
        """落地：有設定 land 就播一次（演完 len(frames)/fps 秒）。"""
        aset = self.config.reactions.get("land")
        if aset is None:
            return
        self._enter("land", now)
        self._end = now + len(aset.frames) / aset.fps

    def _pick_occasional(self) -> str:
        names = list(self.config.occasional)
        weights = [self.config.occasional[n].weight for n in names]
        return self.rng.choices(names, weights=weights, k=1)[0]

    def step(self, now: float, talking: bool, walking: bool, allow_occasional: bool = True,
             dragging: bool = False, falling: bool = False):
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

        reactions = self.config.reactions
        for flag, name in ((dragging, "drag"), (falling, "fall")):
            if flag and name in reactions:  # 持續型反應：條件在就一直演，優先於一切（除了說話）
                if self.key != name:
                    self._enter(name, now)
                return self.key, self.frame_index(now)
        if self.key in ("drag", "fall"):  # 條件消失
            self._leave_to_idle(now)
        if self.key in ("poke", "land") and self.key in reactions:
            if now < self._end:
                return self.key, self.frame_index(now)
            self._leave_to_idle(now)

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

    def elapsed(self, now: float) -> float:
        """進入目前狀態後經過的秒數（給 pet_anim_fx 算身體動作用）。"""
        return 0.0 if self._start is None else now - self._start

    def frame_index(self, now: float) -> int:
        aset = self._set_for(self.key)
        idx = int((now - self._start) * aset.fps)
        n = len(aset.frames)
        return idx % n if aset.loop else min(idx, n - 1)
