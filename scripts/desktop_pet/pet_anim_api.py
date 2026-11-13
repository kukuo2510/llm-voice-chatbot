"""AnimController 的多段動作對外 API 與進階抽籤條件（mixin，純邏輯，不碰 Tk）。

- 抽籤條件：hours（時段）、after_idle_sec（距離最後一次互動）、weight 0（不進抽籤）。
- 外部控制：start(name) 強制開始、stop(name) 結束、current_sequence、is_sleeping、anchor_y。
- 邊緣坐下：placement "edge" 的動作抽中後先進入「待命走位」（pending_edge），
  由物理走到邊緣後呼叫 edge_arrived() 才真的開始演。
只能由 pet_anim.AnimController 繼承使用（會用到它的 _seq／_enter／_cancel_seq 等）。
"""

from pet_anim_seq import SequenceRun
from pet_timeofday import in_hours

EDGE_TIMEOUT_SEC = 25.0  # 走到邊緣最多等這麼久，走不到（被擋住等）就放棄
WALK_IGNORE_SEC = 0.5  # 強制開始／走到邊緣的瞬間，物理的「正在走」還有餘波，這段時間內不拿它取消動作


class SequenceApiMixin:
    # ---- 狀態查詢 ----
    @property
    def current_sequence(self):
        """目前在演的多段動作名稱；沒有回傳 None（還在走向邊緣的不算，見 pending_edge）。"""
        return None if self._seq is None else self._seq.sdef.name

    @property
    def is_sleeping(self) -> bool:
        """正在演標了 sleeping 的多段動作（主動搭話、通知要略過）。"""
        return self._seq is not None and self._seq.sdef.sleeping

    @property
    def anchor_y(self):
        """目前這段的 anchor_y（0~1）；不在演多段動作、或這段沒設回傳 None。"""
        return None if self._seq is None else self._seq.phase.anchor_y

    @property
    def pending_edge(self):
        """正在走向邊緣、等著開演的多段動作名稱；沒有回傳 None。"""
        return self._pending

    # ---- 互動紀錄 ----
    def note_interaction(self, now=None) -> None:
        """記錄一次使用者互動（說話、點擊、拖曳、輸入列開啟）；step／poke／wake_click 會自動呼叫。"""
        self._last_interact = self._mono() if now is None else now

    # ---- 抽籤池 ----
    def _pick_pool(self, now: float):
        """符合條件（權重>0、時段、閒置夠久）的 (名稱清單, 權重清單)；順序：小動作在前、多段動作在後。"""
        dt = self._clock()
        idle_for = float("inf") if self._last_interact is None else now - self._last_interact
        names, weights = [], []
        for name, item in self.config.occasional.items():
            if item.weight > 0 and in_hours(item.hours, dt):
                names.append(name)
                weights.append(item.weight)
        for name, sdef in self.config.sequences.items():
            if sdef.weight > 0 and in_hours(sdef.hours, dt) and idle_for >= sdef.after_idle_sec:
                names.append(name)
                weights.append(sdef.weight)
        return names, weights

    # ---- 開始／結束 ----
    def _begin_run(self, name: str, now: float, forced: bool) -> None:
        self._pending = None
        self._seq = SequenceRun(self.config.sequences[name], now, self.rng, forced)
        self.key, self._start, self._end = self._seq.key, now, None
        self._ignore_walk_until = now + WALK_IGNORE_SEC

    def _launch(self, name: str, now: float, forced: bool) -> None:
        """取消目前的小動作／多段動作，開始 name（placement edge 的先進入待命走位）。"""
        self._queued = None
        self._seq = None
        self._pending = None
        self._enter("idle", now)
        self._ignore_walk_until = now + WALK_IGNORE_SEC
        if self.config.sequences[name].placement == "edge":
            self._pending, self._pending_since = name, now
            self._pending_forced, self._at_edge = forced, False
        else:
            self._begin_run(name, now, forced)

    def start(self, name: str, now=None) -> bool:
        """強制開始多段動作 name（含 weight 0 的；不管 hours／after_idle_sec／目前抽籤條件）。

        會取消目前的小動作、多段動作與走位。說話中：不打斷她講話，記下來等講完（下一次 step 不再說話時）才開始。
        名稱不存在回傳 False。計為一次使用者互動。
        """
        if name not in self.config.sequences:
            return False
        now = self._mono() if now is None else now
        self._last_interact = now
        if self._talking:
            self._queued = name
        else:
            self._launch(name, now, True)
        return True

    def stop(self, name: str, now=None) -> bool:
        """結束 name：有 exit 段就跳過去演完；沒有就直接回 idle。沒有在演（或排隊）它回傳 False。"""
        now = self._mono() if now is None else now
        if self._queued == name:
            self._queued = None
            return True
        if self._pending == name:
            self._pending = None
            self._schedule_next(now)
            return True
        if self._seq is None or self._seq.sdef.name != name:
            return False
        if self._seq.stop(now):
            self.key, self._start = self._seq.key, self._seq.start
        else:
            self._cancel_seq(now)
        return True

    def edge_arrived(self, now=None) -> None:
        """物理回報：已走到邊緣、面向也轉好了。下一次 step 就會開演。"""
        if self._pending is not None:
            self._at_edge = True

    def _clear_pending(self) -> None:
        self._pending = None
        self._queued = None

    def _service_requests(self, now: float) -> None:
        """step 裡處理排隊的 start() 與邊緣走位（到了就開演、等太久就放棄）。"""
        if self._queued is not None:
            self._launch(self._queued, now, True)
        elif self._pending is not None:
            if self._at_edge:
                self._begin_run(self._pending, now, self._pending_forced)
            elif now - self._pending_since > EDGE_TIMEOUT_SEC:
                self._pending = None
                self._schedule_next(now)
