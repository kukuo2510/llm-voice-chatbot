"""讀取角色的動畫設定 characters/<角色>/avatar/animations.json。

這個檔是選用的：沒有它（例如預設的 sakiko）就回傳 None，桌寵行為跟沒有動畫功能時完全一樣。

格式（frames 路徑相對於 avatar 資料夾）：
    {
      "schemaVersion": 1,
      "reference": "idle",                      # 可省略；縮放基準的狀態（預設 idle）
      "occasional_interval_sec": [20, 60],      # 閒置時隨機演出小動作的間隔
      "states":   {"idle": SET, "walk": SET},   # idle 必填，walk 選填
      "occasional": {"drink-tea": SET, ...},    # 選填，閒置時隨機挑一個演
      "reactions": {"poke": SET, "drag": SET,   # 選填，被觸發才演；四個各自選填
                    "fall": SET, "land": SET}
    }
    SET = {"frames": [...], "fps": 8, "loop": true,
           "native_facing": "front" | "left" | "right",   # 圖本身面向；front 永不翻轉
           "duration_sec": [4.5, 7.5],           # 只有 loop 的小動作用：演多久
           "weight": 1}                          # 小動作被挑中的權重
    reactions 說明：
      poke ：點臉。SET 另外可帶 "sequence_sec": [0.75, 1.0, 2.5]（連戳第 0/1/2 級各演多久，
             超過就停在最後一級）、"reset_after_sec": 2.0（隔多久沒戳就從頭）、
             "cooldown_sec": 0.3（兩次戳之間的最短間隔，太快的忽略）；loop 預設 false
      drag ：被拖曳時（按住並移動）；fall：掉落中；land：掉落後落地的一次性動作
    沒有 reactions 時，格式與行為跟以前完全一樣。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_INTERVAL = (20.0, 60.0)
DEFAULT_LOOP_DURATION = (4.0, 7.0)
DEFAULT_POKE_SEQUENCE = (0.75, 1.0, 2.5)  # 連戳第 0/1/2 級的演出秒數
DEFAULT_POKE_RESET_SEC = 2.0
DEFAULT_POKE_COOLDOWN_SEC = 0.3
REACTION_NAMES = ("poke", "drag", "fall", "land")
_FACINGS = ("front", "left", "right")


@dataclass(frozen=True)
class AnimSet:
    name: str
    frames: tuple  # tuple[Path, ...]
    fps: float
    loop: bool
    native_facing: str
    duration: tuple  # (min, max) 秒，loop 的小動作才用
    weight: float


@dataclass(frozen=True)
class AnimConfig:
    reference: str
    interval: tuple
    states: dict
    occasional: dict
    reactions: dict = field(default_factory=dict)  # 被觸發才演的動作：poke/drag/fall/land
    poke_sequence: tuple = DEFAULT_POKE_SEQUENCE  # 連戳各級的演出秒數
    poke_reset_sec: float = DEFAULT_POKE_RESET_SEC
    poke_cooldown_sec: float = DEFAULT_POKE_COOLDOWN_SEC


def _pair(value, default) -> tuple:
    try:
        lo, hi = float(value[0]), float(value[1])
        if 0 < lo <= hi:
            return lo, hi
    except (TypeError, ValueError, IndexError, KeyError):
        pass
    return default


def _parse_set(name: str, raw: dict, base: Path):
    """解析一組動畫；任何問題（缺檔、格式錯）回傳 None 並印出原因，不丟例外。"""
    try:
        files = raw["frames"]
        if not files:
            raise ValueError("frames 是空的")
        paths = []
        base_resolved = base.resolve()
        for f in files:
            p = (base / f).resolve()
            if base_resolved not in p.parents:
                raise ValueError(f"路徑跑出 avatar 資料夾外面：{f}")
            if not p.is_file():
                raise ValueError(f"找不到檔案：{f}")
            paths.append(p)
        fps = float(raw.get("fps", 8))
        if fps <= 0:
            raise ValueError("fps 必須大於 0")
        facing = raw.get("native_facing", "front")
        if facing not in _FACINGS:
            raise ValueError(f"native_facing 只能是 {_FACINGS}")
        return AnimSet(
            name=name,
            frames=tuple(paths),
            fps=fps,
            loop=bool(raw.get("loop", True)),
            native_facing=facing,
            duration=_pair(raw.get("duration_sec"), DEFAULT_LOOP_DURATION),
            weight=max(0.0, float(raw.get("weight", 1))),
        )
    except (KeyError, TypeError, ValueError) as exc:
        print(f"[anim] 略過動畫 {name!r}：{exc}")
        return None


def _positive(value, default: float) -> float:
    """正數才採用，否則用預設。"""
    try:
        v = float(value)
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    return default


def _poke_sequence(value) -> tuple:
    """解析 sequence_sec：要是非空、全為正數的清單，否則用預設。"""
    try:
        seq = tuple(float(x) for x in value)
        if seq and all(x > 0 for x in seq):
            return seq
    except (TypeError, ValueError):
        pass
    return DEFAULT_POKE_SEQUENCE


def _parse_reactions(raw, base: Path):
    """解析 reactions 區塊，回傳 (reactions, poke 參數 dict)；壞掉的單項略過。"""
    reactions, poke = {}, {}
    if raw is None:
        return reactions, poke
    if not isinstance(raw, dict):
        print("[anim] 略過 reactions：格式不是物件")
        return reactions, poke
    for name in REACTION_NAMES:
        if name not in raw:
            continue
        item = raw[name]
        if not isinstance(item, dict):
            print(f"[anim] 略過動畫 {name!r}：格式不是物件")
            continue
        item = {"loop": name == "fall", **item}  # 沒寫 loop 時：fall 循環，其餘播一次
        parsed = _parse_set(name, item, base)
        if parsed:
            reactions[name] = parsed
            if name == "poke":
                poke = {
                    "poke_sequence": _poke_sequence(item.get("sequence_sec")),
                    "poke_reset_sec": _positive(item.get("reset_after_sec"), DEFAULT_POKE_RESET_SEC),
                    "poke_cooldown_sec": _positive(item.get("cooldown_sec"), DEFAULT_POKE_COOLDOWN_SEC),
                }
    return reactions, poke


def load_animations(avatar_dir: Path):
    """回傳 AnimConfig；沒有 animations.json、檔案壞掉、或缺 idle 時回傳 None。"""
    path = Path(avatar_dir) / "animations.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_states = data.get("states", {})
        raw_occ = data.get("occasional", {})
    except (OSError, ValueError, AttributeError) as exc:
        print(f"[anim] animations.json 讀取失敗，停用動畫：{exc}")
        return None

    states = {}
    for name, raw in raw_states.items():
        parsed = _parse_set(name, raw, Path(avatar_dir))
        if parsed:
            states[name] = parsed
    if "idle" not in states:
        print("[anim] animations.json 沒有可用的 idle，停用動畫")
        return None

    occasional = {}
    for name, raw in raw_occ.items():
        parsed = _parse_set(name, raw, Path(avatar_dir))
        if parsed and parsed.weight > 0:
            occasional[name] = parsed

    reference = data.get("reference", "idle")
    if reference not in states:
        reference = "idle"
    reactions, poke = _parse_reactions(data.get("reactions"), Path(avatar_dir))
    return AnimConfig(
        reference=reference,
        interval=_pair(data.get("occasional_interval_sec"), DEFAULT_INTERVAL),
        states=states,
        occasional=occasional,
        reactions=reactions,
        **poke,
    )
