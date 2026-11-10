"""立繪的滑鼠互動：按下／拖曳／放開、右鍵選單、滾輪縮放。

從 desktop_pet.py 的 CharacterWindow 原封不動搬出來的方法（self.* 全部照舊）。
不能 import desktop_pet（會循環），要用的常數／函式從 pet_config 等模組拿。
"""

import tkinter as tk

from pet_config import (
    ASSETS_DIR,
    AVATAR_MAX_WIDTH,
    AVATAR_MIN_WIDTH,
    AVATAR_SCROLL_STEP,
    DRAG_THRESHOLD_PX,
    _AVATAR_RESIZE_DEBOUNCE_MS,
)
from pet_prefs import _save_prefs
from pet_shell import load_avatar_frames


class AvatarInteractionMixin:
    """立繪的滑鼠互動：按下／拖曳／放開、右鍵選單、滾輪縮放。"""

    # ============================================================
    # 立繪拖曳 / 點擊
    # ============================================================

    def _on_avatar_press(self, event: tk.Event) -> None:
        """滑鼠左鍵按下：記錄起點座標，先假設這是「點擊」（還沒被判定為拖曳）。"""
        self._dragged = False
        # 按著的這段時間，物理 tick 會暫停，放開後再從當下位置繼續掉落。
        self._mouse_down = True
        self._drag_origin = (event.x_root, event.y_root)
        self._win_origin = (self.root.winfo_x(), self.root.winfo_y())

    def _on_avatar_motion(self, event: tk.Event) -> None:
        """滑鼠按住移動：跟著移動視窗位置，並判斷是否已經超過拖曳門檻。"""
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]

        if abs(dx) > DRAG_THRESHOLD_PX or abs(dy) > DRAG_THRESHOLD_PX:
            self._dragged = True

        self.root.geometry(f"+{self._win_origin[0] + dx}+{self._win_origin[1] + dy}")
        self._position_input_popup()

    def _on_avatar_release(self, event: tk.Event) -> None:
        """滑鼠放開：如果整個過程沒有被判定為拖曳，就當成一次「點擊」，打開輸入列。"""
        self._mouse_down = False
        # 放開的瞬間重新開始加速掉落，不要延續拖曳前累積的下墜速度，
        # 不然拖到高處放開會突然「飛」出去很奇怪。
        self._fall_velocity = 0.0

        if not self._dragged:
            if self._anim_ctrl is not None and self._try_poke(event.y):
                return  # 點在臉上：戳一下，不開輸入列
            self._toggle_input_popup()

    def _on_avatar_right_click(self, event: tk.Event) -> None:
        """滑鼠右鍵：彈出右鍵選單。"""
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def _on_avatar_scroll(self, event: tk.Event) -> None:
        """滾輪縮放立繪大小：往上滾放大、往下滾縮小，範圍限制在
        AVATAR_MIN_WIDTH ~ AVATAR_MAX_WIDTH 之間。

        這裡只更新「目標寬度」並排程一次重繪，真正的重繪（縮放圖片、
        改變視窗大小）會延到 _apply_avatar_resize 才執行——滾很快時
        會連續觸發一堆滾輪事件，如果每個事件都立刻重繪，畫面會很卡頓。
        改成「滾動停下來一小段時間（_AVATAR_RESIZE_DEBOUNCE_MS）之後
        才真的畫一次」，中途只是更新一個數字，運算成本很低。
        """
        direction = 1 if event.delta > 0 else -1
        new_width = max(
            AVATAR_MIN_WIDTH,
            min(AVATAR_MAX_WIDTH, self._pending_avatar_width + direction * AVATAR_SCROLL_STEP),
        )

        if new_width == self._pending_avatar_width:
            return

        self._pending_avatar_width = new_width

        if self._avatar_resize_job is not None:
            self.root.after_cancel(self._avatar_resize_job)
        self._avatar_resize_job = self.root.after(_AVATAR_RESIZE_DEBOUNCE_MS, self._apply_avatar_resize)

    def _apply_avatar_resize(self) -> None:
        """滾輪停下來一小段時間後才真的執行的重繪。

        步驟：縮放／去背圖片（原始素材已經快取過，這一步不需要重讀磁碟，
        見 pet_shell.py 的 load_avatar_source 說明）、改變視窗大小、
        把這次選擇的大小記住，下次啟動時沿用。
        """
        self._avatar_resize_job = None

        if self._pending_avatar_width == self._avatar_width:
            return

        self._avatar_width = self._pending_avatar_width

        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.frame_closed, self.frame_open, avatar_height = load_avatar_frames(
            ASSETS_DIR, self._avatar_width
        )
        self.frame_closed_l, self.frame_open_l, _ = load_avatar_frames(
            ASSETS_DIR, self._avatar_width, mirror=True
        )
        self.avatar_label.configure(image=self._current_frame(is_open=False))
        self.root.geometry(f"{self._avatar_width}x{avatar_height}+{x}+{y}")
        self.dialogue_label.configure(wraplength=self._avatar_width - 24)
        self._anim_on_resize()
        self._position_input_popup()
        _save_prefs(avatar_width=self._avatar_width)
