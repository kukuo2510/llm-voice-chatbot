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
from pet_anim_fx import IDENTITY, apply_fx, fx_for
from pet_config import ASSETS_DIR
import livliv_link

ANIM_TICK_MS = 40  # 約 25fps 的檢查頻率，實際換幀依各組的 fps
FX_CACHE_MAX = 400  # 變形後幀的快取上限（參數有量化，實際用到的組合不多）
WALK_GRACE_SEC = 0.15  # 最近這麼久內有水平移動就算「正在走」


class AnimationMixin:
    """立繪幀動畫（idle／walk／偶發小動作／觸發式反應）。"""

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

    def _anim_frames(self, key: str):
        """這組動畫正規化後的 PIL 幀（依目前寬度、面向快取）。"""
        cfg = self._anim_cfg
        aset = cfg.set_for(key)
        mirrored = needs_mirror(aset.native_facing, self._facing)
        ck = (self._avatar_width, key, mirrored)
        frames = self._anim_photos.get(ck)
        if frames is None:
            frames = build_set(aset, ASSETS_DIR, self._avatar_width, mirrored)
            self._anim_photos[ck] = frames
        return ck, frames

    def _anim_photo(self, key: str, idx: int, fx=IDENTITY):
        """取得要顯示的 PhotoImage：幀 idx 套上變形 fx（見 pet_anim_fx）。"""
        ck, frames = self._anim_frames(key)
        pk = ("photo", *ck, idx, fx)
        photo = self._anim_photos.get(pk)
        if photo is None:
            if len(self._anim_photos) > FX_CACHE_MAX:
                self._anim_photos = {k: v for k, v in self._anim_photos.items() if k[0] != "photo"}
            photo = ImageTk.PhotoImage(colorkey_rgb(apply_fx(frames[idx], fx)))
            self._anim_photos[pk] = photo
        return photo

    def _anim_tick(self) -> None:
        try:
            now = time.monotonic()
            talking = self._speaking  # 2026-11-10：原本用 _busy，想回覆／翻譯的整段都卡在靜態圖
            walking = now - self._walk_moved_at < WALK_GRACE_SEC
            result = self._anim_ctrl.step(
                now, talking, walking, allow_occasional=self._input_popup is None,
                dragging=self._mouse_down and self._dragged, falling=self._falling,
            )
            if result is None:
                if self._anim_last is not None:  # 剛開始說話：回到靜態立繪，嘴型換圖從這裡接手
                    self.avatar_label.configure(image=self._current_frame(is_open=False))
                    self._anim_last = None
            else:
                fx = fx_for(result[0], self._anim_ctrl.elapsed(now))
                state = (*result, fx, self._facing, self._avatar_width)
                if state != self._anim_last:
                    self.avatar_label.configure(image=self._anim_photo(*result, fx))
                    self._anim_last = state
        except Exception as exc:  # 動畫壞了就整個停用，退回靜態立繪
            print(f"[anim] 動畫停用：{exc!r}")
            self._anim_ctrl = None
            self.avatar_label.configure(image=self._current_frame(is_open=False))
            return
        self.root.after(ANIM_TICK_MS, self._anim_tick)
