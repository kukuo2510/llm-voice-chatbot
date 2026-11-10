"""把動畫狀態機（pet_anim）接到桌寵視窗：定時換立繪幀。

只有角色資料夾有 animations.json、而且不是舞台模式才會啟動；否則 _anim_ctrl 維持 None，
所有行為跟沒有動畫功能時完全一樣（連 after() 排程都不會多一個）。
說話中不動（嘴型換圖照舊）；走路動畫只在物理真的水平移動時播（見 pet_physics 的 _walk_moved_at）。
"""

import time

from PIL import ImageTk

from pet_anim import AnimController
from pet_anim_data import load_animations
from pet_anim_frames import build_set, colorkey_rgb, needs_mirror
from pet_config import ASSETS_DIR
import livliv_link

ANIM_TICK_MS = 40  # 約 25fps 的檢查頻率，實際換幀依各組的 fps
WALK_GRACE_SEC = 0.15  # 最近這麼久內有水平移動就算「正在走」


class AnimationMixin:
    """立繪幀動畫（idle／walk／偶發小動作）。"""

    _anim_ctrl = None
    _anim_cfg = None
    _anim_photos: dict = None
    _anim_last = None
    _walk_moved_at = -1e9

    def _setup_animation(self) -> None:
        if livliv_link.ENABLED:
            return
        cfg = load_animations(ASSETS_DIR)
        if cfg is None:
            return
        self._anim_cfg = cfg
        self._anim_ctrl = AnimController(cfg)
        self._anim_photos = {}
        self.root.after(ANIM_TICK_MS, self._anim_tick)

    def _anim_on_resize(self) -> None:
        """滾輪縮放後，快取的幀尺寸作廢，下一個 tick 依新寬度重算。"""
        if self._anim_ctrl is not None:
            self._anim_photos = {}
            self._anim_last = None

    def _anim_photo(self, key: str, idx: int):
        aset = self._anim_cfg.states.get(key) or self._anim_cfg.occasional[key]
        mirrored = needs_mirror(aset.native_facing, self._facing)
        ck = (self._avatar_width, key, mirrored)
        photos = self._anim_photos.get(ck)
        if photos is None:
            frames = build_set(aset, ASSETS_DIR, self._avatar_width, mirrored)
            photos = [ImageTk.PhotoImage(colorkey_rgb(f)) for f in frames]
            self._anim_photos[ck] = photos
        return photos[idx]

    def _anim_tick(self) -> None:
        try:
            now = time.monotonic()
            talking = self._busy
            walking = now - self._walk_moved_at < WALK_GRACE_SEC
            result = self._anim_ctrl.step(now, talking, walking, allow_occasional=self._input_popup is None)
            if result is None:
                if self._anim_last is not None:  # 剛開始說話：回到靜態立繪，嘴型換圖從這裡接手
                    self.avatar_label.configure(image=self._current_frame(is_open=False))
                    self._anim_last = None
            else:
                state = (*result, self._facing, self._avatar_width)
                if state != self._anim_last:
                    self.avatar_label.configure(image=self._anim_photo(*result))
                    self._anim_last = state
        except Exception as exc:  # 動畫壞了就整個停用，退回靜態立繪
            print(f"[anim] 動畫停用：{exc!r}")
            self._anim_ctrl = None
            self.avatar_label.configure(image=self._current_frame(is_open=False))
            return
        self.root.after(ANIM_TICK_MS, self._anim_tick)
