"""共用的 .env 載入邏輯。

任何需要讀取 LLM_PROVIDER / STT_PROVIDER / API key 之類設定的模組，
都應該在自己檔案最上面呼叫 load_dotenv()，不要依賴「呼叫端（例如
desktop_pet.py、chat_cli.py……）有沒有先載入、載入順序對不對」。

背景：曾經發生過呼叫端根本沒載入 .env，或是載入時機太晚
（例如 chat.py 已經把 LLM_PROVIDER 讀成預設值之後，才載入 .env）
導致 .env 裡的設定悄悄沒生效的問題。

解法：改成「誰需要這些設定，誰就自己在最上面呼叫 load_dotenv()」，
就不必再管呼叫端的 import 順序，也不怕漏掉。
"""

import os
from pathlib import Path

# 專案根目錄：從目前檔案位置往上推兩層
# （common/env.py -> scripts/ -> platform/），
# 藉此推算出 .env 應該放在哪裡，不用寫死絕對路徑。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 模組層級的旗標：確保就算被多個地方重複呼叫 load_dotenv()，
# 實際讀檔的動作也只會發生一次。
_loaded = False


def load_dotenv() -> None:
    """讀取專案根目錄的 .env，把裡面的變數塞進 os.environ。

    行為重點：
    - 若某個 key 已經存在於 os.environ（例如系統環境變數、或執行時
      用 `set FOO=bar` 手動指定），不會被 .env 裡的值覆蓋掉——
      這是刻意設計，讓外部環境變數的優先權高於 .env 檔案。
    - 可以放心重複呼叫；只有第一次呼叫會真的去讀檔案，
      之後的呼叫會直接因為 _loaded 為 True 而立刻返回。
    """
    global _loaded

    # 已經載入過了，不用再重複解析檔案。
    if _loaded:
        return
    _loaded = True

    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        # 沒有 .env 也不是錯誤（例如全部改用系統環境變數的情境），
        # 靜默略過即可。
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        # 跳過空行、註解行（# 開頭），以及格式不對（沒有 "=" 的）的行。
        if not line or line.startswith("#") or "=" not in line:
            continue

        # 用第一個 "=" 切開 key 和 value，這樣即使 value 裡面
        # 本身含有 "=" 字元（例如某些 API URL 的 query string）
        # 也不會被錯誤地再切一次。
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        if key and key not in os.environ:
            os.environ[key] = value
