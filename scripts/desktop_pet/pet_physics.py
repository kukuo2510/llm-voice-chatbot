"""桌寵物理：重力落地、自主漫遊、依面向挑立繪。

從 desktop_pet.py 的 CharacterWindow 原封不動搬出來的方法（self.* 全部照舊）。
不能 import desktop_pet（會循環），要用的常數／函式從 pet_config 等模組拿。
"""

import random
import time

from pet_config import (
    FALL_TERMINAL_VELOCITY_PX_S,
    GRAVITY_PX_S2,
    PHYSICS_TICK_MS,
    WANDER_MAX_DURATION_SEC,
    WANDER_MAX_INTERVAL_SEC,
    WANDER_MIN_DURATION_SEC,
    WANDER_MIN_INTERVAL_SEC,
    WANDER_SPEED_PX_S,
)
import desktop_physics


LAND_MIN_FALL_SEC = 0.25  # 掉落至少這麼久，落地才算「摔到地上」（避免微小落差也觸發落地動作）


def wander_paused(busy: bool, popup_open: bool, resting: bool) -> bool:
    """漫遊要不要暫停：忙碌、輸入列開著、或正在演多段動作（睡覺中）。"""
    return busy or popup_open or resting


class PhysicsMixin:
    """桌寵物理：重力落地、自主漫遊、依面向挑立繪。"""

    _falling = False  # 目前是否在掉落中（動畫用）
    _fall_since = 0.0

    def _on_landed(self) -> None:
        """掉落後落地的掛鉤，預設什麼都不做（pet_reactions 會覆寫來播落地動作）。"""

    # ============================================================
    # 桌寵物理（重力 / 落地，見 desktop_physics.py）
    # ============================================================

    def _physics_tick(self) -> None:
        """物理模擬的每一個 tick：計算重力下墜或自主漫遊後的新位置。

        每個 tick 都重新問一次「正下方最近的平台在哪」，完全不快取任何
        「目前站在哪個視窗上」的狀態——平台被關掉或移走，下一個 tick
        自然就會開始往下一個平台掉，不需要另外偵測「平台消失了」這種
        特殊情況。

        按著角色拖曳時（見 _mouse_down）會暫停這個計算，放開後從放開
        當下的位置繼續掉落。只有在「已經落地、沒在掉落中」的狀態，
        才會觸發自主漫遊（見 _advance_wander）。
        """
        if self._physics_enabled_var.get() and not self._mouse_down:
            dt = PHYSICS_TICK_MS / 1000
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            w = self.root.winfo_width()
            h = self.root.winfo_height()

            surface_y = desktop_physics.find_landing_y(x, x + w, y + h, self._hwnd)
            falling = y + h < surface_y - 1
            self._update_fall_state(falling)

            if falling:
                self._fall_velocity = min(
                    self._fall_velocity + GRAVITY_PX_S2 * dt,
                    FALL_TERMINAL_VELOCITY_PX_S,
                )
                new_y = min(y + self._fall_velocity * dt, surface_y - h)
                new_x = x
            else:
                self._fall_velocity = 0.0
                new_y = surface_y - h
                new_x = self._advance_wander(x, w, dt)
                if new_x != x:
                    self._walk_moved_at = time.monotonic()  # 給走路動畫判斷「真的在水平移動」

            if new_x != x or new_y != y:
                self.root.geometry(f"+{int(new_x)}+{int(new_y)}")
                self._position_input_popup()

        else:
            self._falling = False  # 被抓住或物理關閉：不算掉落

        self.root.after(PHYSICS_TICK_MS, self._physics_tick)

    def _update_fall_state(self, falling: bool) -> None:
        """維護 _falling；掉落 >= LAND_MIN_FALL_SEC 後落地時呼叫 _on_landed。"""
        now = time.monotonic()
        if falling and not self._falling:
            self._fall_since = now
        elif self._falling and not falling and now - self._fall_since >= LAND_MIN_FALL_SEC:
            self._falling = False
            self._on_landed()
        self._falling = falling

    def _advance_wander(self, x: int, w: int, dt: float) -> float:
        """落地時偶爾隨機左右滑動一小段，讓角色感覺「活著」，不是釘死在原地。

        這裡沿用現有的靜態立繪、用左右翻轉表示面向方向（見 _current_frame），
        並不是真的走路分格動畫。

        忙碌中（正在生成回覆）或輸入列開著的時候，會暫停倒數／移動，
        避免角色聊到一半突然自己走掉，體感很奇怪。
        """
        ctrl = getattr(self, "_anim_ctrl", None)
        if wander_paused(self._busy, self._input_popup is not None, ctrl is not None and ctrl.resting):
            return x

        if self._walk_seconds_left > 0:
            self._walk_seconds_left -= dt
            step = WANDER_SPEED_PX_S * dt * (1 if self._facing == "right" else -1)
            screen_w = self.root.winfo_screenwidth()
            new_x = max(0, min(x + step, screen_w - w))

            if new_x == x:
                # 撞到螢幕邊界走不動了，提早結束這次漫遊。
                self._walk_seconds_left = 0.0

            return new_x

        if time.monotonic() >= self._next_wander_at:
            self._start_wander()

        return x

    def _start_wander(self) -> None:
        """開始新的一輪自主漫遊：隨機決定方向與持續時間，並排定下一次漫遊的時間。"""
        self._facing = random.choice(("left", "right"))
        self.avatar_label.configure(image=self._current_frame(is_open=False))
        self._walk_seconds_left = random.uniform(WANDER_MIN_DURATION_SEC, WANDER_MAX_DURATION_SEC)
        self._next_wander_at = time.monotonic() + random.uniform(
            WANDER_MIN_INTERVAL_SEC, WANDER_MAX_INTERVAL_SEC
        )

    def _current_frame(self, is_open: bool):
        """依目前面向方向與嘴巴開闔狀態，回傳對應的立繪圖片物件。"""
        if self._facing == "left":
            return self.frame_open_l if is_open else self.frame_closed_l
        return self.frame_open if is_open else self.frame_closed
