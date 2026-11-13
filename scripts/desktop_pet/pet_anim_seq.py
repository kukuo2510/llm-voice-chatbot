"""多段式小動作（sequences）：解析與執行（純邏輯，不碰 Tk；時間與亂數從外面傳入）。

animations.json 的選用區塊：
    "sequences": {
      "penguin-nap": {
        "weight": 2, "native_facing": "front", "wake_on_click": true,
        "phases": [
          {"frames": [...], "fps": 1.5, "loop": false, "duration_sec": [9.2, 14.2]},
          {"frames": [...], "fps": 1.5, "loop": false, "wake": true}
        ]
      }
    }
每一段（phase）各自是一組幀，內部 key 叫 "<名稱>#<段序>"（例如 "penguin-nap#0"），
所以既有的幀快取、fx 查表都照舊能用。至多一段可標 "wake": true（醒來那段）；
wake_on_click 為真時，在醒來那段「之前」點擊會直接跳到醒來那段。

選用的進階欄位（壞的單項整個多段動作略過，不影響其他）：
  頂層  hours: [23, 7]        只有本機時間落在範圍內才進抽籤池（可跨午夜，見 pet_timeofday）
  頂層  after_idle_sec: 600   距離最後一次使用者互動至少這麼久才可抽中
  頂層  sleeping: true        演的時候 ctrl.is_sleeping 為真（主動搭話／通知略過）
  頂層  placement: "edge"     抽中後先走到支撐物最近的邊緣，到了才開始（第 0 段標也算）
  頂層  weight: 0             不進抽籤，只能用 ctrl.start(名稱) 強制開始
  phase until: "wake"         不看 duration，循環到被點醒；有 hours 時離開時段也自然起床（跳到 wake 段）
  phase until: "stop"         循環到外部 ctrl.stop(名稱)，跳到標 exit 的段（沒有就直接結束）
  phase exit: true            stop 時要跳去的那一段（至多一段）
  phase anchor_y: 0.69        這段圖中「支撐線」在內容高度的比例；物理讓視窗下沉使支撐線對齊落腳面
"""

from dataclasses import dataclass

from pet_timeofday import parse_hours

UNTIL_VALUES = ("wake", "stop")


@dataclass(frozen=True)
class Phase:
    key: str  # "<名稱>#<段序>"
    aset: object  # AnimSet
    duration: object  # (min, max) 秒或 None；沒給就用 len(frames)/fps
    wake: bool
    until: object = None  # None／"wake"／"stop"：不看 duration，等事件才結束
    anchor_y: object = None  # None 或 0~1：支撐線在內容高度的比例
    exit: bool = False  # ctrl.stop() 時要跳來的那一段


@dataclass(frozen=True)
class SequenceDef:
    name: str
    phases: tuple
    weight: float
    wake_on_click: bool
    hours: object = None  # (開始時, 結束時) 或 None
    after_idle_sec: float = 0.0
    sleeping: bool = False
    placement: object = None  # None 或 "edge"

    def _index_of(self, attr: str):
        for i, p in enumerate(self.phases):
            if getattr(p, attr):
                return i
        return None

    @property
    def wake_index(self):
        return self._index_of("wake")

    @property
    def exit_index(self):
        return self._index_of("exit")


def _duration(value):
    """duration_sec：沒給回傳 None；給了就要是 0 < min <= max，否則丟 ValueError。"""
    if value is None:
        return None
    lo, hi = float(value[0]), float(value[1])
    if not 0 < lo <= hi:
        raise ValueError(f"duration_sec 不合理：{value!r}")
    return lo, hi


def _parse_phase_extras(i: int, rp: dict) -> dict:
    """解析單段的 until／anchor_y／exit；不合理丟 ValueError。"""
    until = rp.get("until")
    if until is not None and until not in UNTIL_VALUES:
        raise ValueError(f"第 {i} 段：until 只能是 {UNTIL_VALUES}：{until!r}")
    anchor = rp.get("anchor_y")
    if anchor is not None:
        if isinstance(anchor, bool) or not isinstance(anchor, (int, float)) or not 0 <= anchor <= 1:
            raise ValueError(f"第 {i} 段：anchor_y 必須是 0~1 的數字：{anchor!r}")
        anchor = float(anchor)
    return {"until": until, "anchor_y": anchor, "exit": bool(rp.get("exit", False))}


def _parse_top_extras(raw: dict) -> dict:
    """解析頂層 hours／after_idle_sec／sleeping／placement；不合理丟 ValueError。"""
    idle = raw.get("after_idle_sec", 0)
    if isinstance(idle, bool) or not isinstance(idle, (int, float)) or idle < 0:
        raise ValueError(f"after_idle_sec 必須是不小於 0 的數字：{idle!r}")
    placement = raw.get("placement")
    if placement is not None and placement != "edge":
        raise ValueError(f"placement 目前只支援 'edge'：{placement!r}")
    return {"hours": parse_hours(raw.get("hours")), "after_idle_sec": float(idle),
            "sleeping": bool(raw.get("sleeping", False)), "placement": placement}


def _parse_sequence(name: str, raw, parse_set) -> SequenceDef:
    """解析單一多段動作；有問題就丟 ValueError（呼叫端會略過並印出原因）。"""
    if not isinstance(raw, dict):
        raise ValueError("格式不是物件")
    raw_phases = raw.get("phases")
    if not isinstance(raw_phases, list) or not raw_phases:
        raise ValueError("phases 必須是非空清單")
    facing = raw.get("native_facing", "front")
    phases = []
    for i, rp in enumerate(raw_phases):
        key = f"{name}#{i}"
        if not isinstance(rp, dict):
            raise ValueError(f"第 {i} 段格式不是物件")
        try:
            duration = _duration(rp.get("duration_sec"))
        except (TypeError, ValueError, IndexError, KeyError) as exc:
            raise ValueError(f"第 {i} 段：{exc}") from exc
        extra = {}
        if raw.get("shared_scale"):  # 各段共用同一個縮放基準（例如沙發：坐下／躺著的沙發一樣大、不跳位）
            extra["norm_frames"] = [f for ph in raw_phases if isinstance(ph, dict) for f in ph.get("frames", [])]
        aset = parse_set(key, {"loop": True, **rp, **extra, "native_facing": facing})
        if aset is None:
            raise ValueError(f"第 {i} 段無法解析")
        extras = _parse_phase_extras(i, rp)
        if rp.get("placement") == "edge":  # 段層級的 placement：第 0 段標了視同整個動作要先走到邊緣
            if i != 0:
                raise ValueError(f"第 {i} 段：placement 只能標在第 0 段（或寫在頂層）")
            raw = {**raw, "placement": "edge"}
        phases.append(Phase(key, aset, duration, bool(rp.get("wake", False)), **extras))
    if sum(p.wake for p in phases) > 1:
        raise ValueError("最多只能有一段 wake")
    if sum(p.exit for p in phases) > 1:
        raise ValueError("最多只能有一段 exit")
    weight = max(0.0, float(raw.get("weight", 1)))
    wake_on_click = bool(raw.get("wake_on_click", False))
    top = _parse_top_extras(raw)
    for i, p in enumerate(phases):
        if p.until == "wake":
            later_wake = any(q.wake for q in phases[i + 1:])
            if not later_wake:
                raise ValueError(f"第 {i} 段 until=wake 後面必須有 wake 段")
            if not wake_on_click and top["hours"] is None:
                raise ValueError(f"第 {i} 段 until=wake 需要 wake_on_click 或 hours，否則永遠醒不來")
    return SequenceDef(name, tuple(phases), weight, wake_on_click, **top)


def parse_sequences(raw, taken, parse_set) -> dict:
    """解析 sequences 區塊，回傳 {名稱: SequenceDef}；壞掉的單項略過、不丟例外。

    taken：已被 states／occasional／reactions 用掉的名稱（撞名就略過）。
    parse_set(key, raw_dict)：解析單組幀，失敗回傳 None（由 pet_anim_data 提供）。
    """
    result = {}
    if raw is None:
        return result
    if not isinstance(raw, dict):
        print("[anim] 略過 sequences：格式不是物件")
        return result
    for name, item in raw.items():
        try:
            if "#" in name:
                raise ValueError("名稱不能含 '#'")
            if name in taken:
                raise ValueError("名稱跟 states／occasional／reactions 重複")
            result[name] = _parse_sequence(name, item, parse_set)  # weight 0 保留：不進抽籤，只能 ctrl.start()
        except (TypeError, ValueError, KeyError) as exc:
            print(f"[anim] 略過多段動作 {name!r}：{exc}")
    return result


class SequenceRun:
    """一次進行中的多段動作：記錄目前第幾段、這段何時結束。"""

    def __init__(self, sdef: SequenceDef, now: float, rng, forced: bool = False):
        self.sdef = sdef
        self._rng = rng
        self.forced = forced  # ctrl.start() 強制開始的：不管 hours，不會因離開時段而自然起床
        self.index = 0
        self._begin(now)

    def _begin(self, now: float) -> None:
        self.start = now
        phase = self.phase
        if phase.duration is not None:
            length = self._rng.uniform(*phase.duration)
        else:
            length = len(phase.aset.frames) / phase.aset.fps
        self.end = now + length

    @property
    def phase(self) -> Phase:
        return self.sdef.phases[self.index]

    @property
    def key(self) -> str:
        return self.phase.key

    def advance(self, now: float, in_hours: bool = True) -> bool:
        """推進到 now；全部演完回傳 False。

        until=wake 的段：不看時間一直循環；有 hours 且已離開時段（in_hours 為假）就自然起床，跳到 wake 段。
        until=stop 的段：一直循環，等 stop()。
        """
        while True:
            phase = self.phase
            if phase.until == "wake":
                w = self.sdef.wake_index
                if (self.sdef.hours is not None and not self.forced and not in_hours
                        and w is not None and self.index < w):
                    self.index = w
                    self._begin(now)
                    continue
                return True
            if phase.until == "stop" or now < self.end:
                return True
            if self.index + 1 >= len(self.sdef.phases):
                return False
            self.index += 1
            self._begin(self.end)

    def stop(self, now: float) -> bool:
        """外部要求結束：有 exit 段就跳過去（已經在那段或更後面就不動）並回傳 True；沒有回傳 False。"""
        e = self.sdef.exit_index
        if e is None:
            return False
        if self.index < e:
            self.index = e
            self._begin(now)
        return True

    def wake(self, now: float) -> bool:
        """點擊喚醒：還沒到醒來那段才跳過去，回傳是否有跳（有跳就該吞掉這次點擊）。"""
        w = self.sdef.wake_index
        if not self.sdef.wake_on_click or w is None or self.index >= w:
            return False
        self.index = w
        self._begin(now)
        return True
