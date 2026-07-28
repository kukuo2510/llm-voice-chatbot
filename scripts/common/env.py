"""共用的 .env 載入邏輯，給任何要讀 LLM_PROVIDER / STT_PROVIDER / API key 這類設定的
模組在自己檔案最上面呼叫，不依賴呼叫端（desktop_pet.py／chat_cli.py／...）記得先載入、
且順序要對——曾經發生過呼叫端根本沒載入 .env、或載入順序太晚（chat.py 已經把
LLM_PROVIDER 讀成預設值之後才載入）導致 .env 裡的設定悄悄沒生效的問題。改成
需要這些設定的模組自己在最上面呼叫 load_dotenv()，就不用管呼叫端的 import 順序。"""
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_loaded = False


def load_dotenv() -> None:
    """讀取專案根目錄的 .env，把裡面的變數塞進 os.environ（若尚未設定過，
    不會覆蓋掉已經存在的系統環境變數）。可以重複呼叫，只有第一次真的讀檔。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value
