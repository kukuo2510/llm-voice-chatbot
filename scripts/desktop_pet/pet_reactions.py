"""觸發式反應的 Tk 接線：點臉被戳、掉落後落地。

動畫狀態機本體在 pet_anim（純邏輯）；這裡只負責「什麼時候呼叫它」。
沒有動畫、或角色沒設定對應的 reaction 時，全部直接略過，行為跟以前完全一樣。
"""

import time

from pet_avatar_metrics import face_zone
from pet_config import ASSETS_DIR


class ReactionMixin:
    """點臉（poke）與落地（land）的觸發。要排在 PhysicsMixin 前面，才能覆寫它的 _on_landed。"""

    def _on_landed(self) -> None:
        """物理偵測到「掉落後落地」：播落地動作（說話中不播）。"""
        ctrl = getattr(self, "_anim_ctrl", None)
        if ctrl is None or self._busy:
            return
        try:
            ctrl.land(time.monotonic())
        except Exception as exc:  # 動畫小事不能拖垮物理迴圈
            print(f"[anim] 落地動作失敗：{exc!r}")

    def _try_poke(self, y: float) -> bool:
        """放開滑鼠時呼叫：點在臉部區且有 poke 反應就戳一下並回傳 True（呼叫端就不開輸入列）。

        回傳 False 代表「這次點擊照舊處理」：沒動畫、沒 poke、點在身體、或說話中。
        點在臉上但在冷卻內時回傳 True（吞掉點擊，不開輸入列）。
        """
        ctrl = getattr(self, "_anim_ctrl", None)
        if ctrl is None or "poke" not in ctrl.config.reactions or self._busy:
            return False
        try:
            top, bottom = face_zone(ASSETS_DIR, self._avatar_width)
        except Exception as exc:
            print(f"[anim] 算不出臉部區，點擊照舊：{exc!r}")
            return False
        if not top <= y <= bottom:
            return False
        ctrl.poke(time.monotonic())
        return True
