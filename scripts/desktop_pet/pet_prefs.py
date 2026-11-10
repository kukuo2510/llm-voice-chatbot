"""桌寵記住的本機偏好（讀寫 scripts/desktop_pet/.pet_prefs.json）。

從 desktop_pet.py 原封不動搬出來的。檔案路徑維持在這個資料夾下的 .pet_prefs.json。
"""

import json
from pathlib import Path

_PET_PREFS_FILE = Path(__file__).resolve().parent / ".pet_prefs.json"


def _load_prefs() -> dict:
    """讀取記住的本機偏好（螢幕讀取開關、對話／STT 走本地或雲端）。

    檔案不存在或內容壞掉時，回傳空字典——呼叫端看到某個 key 不存在時，
    應該回退去用 .env 的預設值，而不是自己去瞎猜一個值。
    """
    if not _PET_PREFS_FILE.exists():
        return {}
    try:
        return json.loads(_PET_PREFS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_prefs(**updates) -> None:
    """把指定的偏好更新，合併寫回本機偏好檔案（保留其他既有的偏好值）。"""
    prefs = _load_prefs()
    prefs.update(updates)
    _PET_PREFS_FILE.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
