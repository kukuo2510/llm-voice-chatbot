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
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Phase:
    key: str  # "<名稱>#<段序>"
    aset: object  # AnimSet
    duration: object  # (min, max) 秒或 None；沒給就用 len(frames)/fps
    wake: bool


@dataclass(frozen=True)
class SequenceDef:
    name: str
    phases: tuple
    weight: float
    wake_on_click: bool

    @property
    def wake_index(self):
        for i, p in enumerate(self.phases):
            if p.wake:
                return i
        return None


def _duration(value):
    """duration_sec：沒給回傳 None；給了就要是 0 < min <= max，否則丟 ValueError。"""
    if value is None:
        return None
    lo, hi = float(value[0]), float(value[1])
    if not 0 < lo <= hi:
        raise ValueError(f"duration_sec 不合理：{value!r}")
    return lo, hi


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
        aset = parse_set(key, {"loop": True, **rp, "native_facing": facing})
        if aset is None:
            raise ValueError(f"第 {i} 段無法解析")
        phases.append(Phase(key, aset, duration, bool(rp.get("wake", False))))
    if sum(p.wake for p in phases) > 1:
        raise ValueError("最多只能有一段 wake")
    weight = max(0.0, float(raw.get("weight", 1)))
    return SequenceDef(name, tuple(phases), weight, bool(raw.get("wake_on_click", False)))


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
            seq = _parse_sequence(name, item, parse_set)
            if seq.weight <= 0:
                continue  # 跟 occasional 一樣：權重 0 代表停用
            result[name] = seq
        except (TypeError, ValueError, KeyError) as exc:
            print(f"[anim] 略過多段動作 {name!r}：{exc}")
    return result


class SequenceRun:
    """一次進行中的多段動作：記錄目前第幾段、這段何時結束。"""

    def __init__(self, sdef: SequenceDef, now: float, rng):
        self.sdef = sdef
        self._rng = rng
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

    def advance(self, now: float) -> bool:
        """推進到 now；全部演完回傳 False。"""
        while now >= self.end:
            if self.index + 1 >= len(self.sdef.phases):
                return False
            self.index += 1
            self._begin(self.end)
        return True

    def wake(self, now: float) -> bool:
        """點擊喚醒：還沒到醒來那段才跳過去，回傳是否有跳（有跳就該吞掉這次點擊）。"""
        w = self.sdef.wake_index
        if not self.sdef.wake_on_click or w is None or self.index >= w:
            return False
        self.index = w
        self._begin(now)
        return True
