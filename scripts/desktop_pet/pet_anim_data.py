"""讀取角色的動畫設定 characters/<角色>/avatar/animations.json。

這個檔是選用的：沒有它（例如預設的 sakiko）就回傳 None，桌寵行為跟沒有動畫功能時完全一樣。

格式（frames 路徑相對於 avatar 資料夾）：
    {
      "schemaVersion": 1,
      "reference": "idle",                      # 可省略；縮放基準的狀態（預設 idle）
      "occasional_interval_sec": [20, 60],      # 閒置時隨機演出小動作的間隔
      "states":   {"idle": SET, "walk": SET},   # idle 必填，walk 選填
      "occasional": {"drink-tea": SET, ...}     # 選填，閒置時隨機挑一個演
    }
    SET = {"frames": [...], "fps": 8, "loop": true,
           "native_facing": "front" | "left" | "right",   # 圖本身面向；front 永不翻轉
           "duration_sec": [4.5, 7.5],           # 只有 loop 的小動作用：演多久
           "weight": 1}                          # 小動作被挑中的權重
"""

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INTERVAL = (20.0, 60.0)
DEFAULT_LOOP_DURATION = (4.0, 7.0)
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
    return AnimConfig(
        reference=reference,
        interval=_pair(data.get("occasional_interval_sec"), DEFAULT_INTERVAL),
        states=states,
        occasional=occasional,
    )
