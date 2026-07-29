"""螢幕讀取：讓桌寵能偶爾看一眼螢幕、把畫面轉成一句文字描述，給
desktop_pet.py 的背景主動搭話迴圈使用。規劃見 TTS通用平台_專案規劃.md 10.5。

看圖跟平常對話刻意分成兩個模型（見規劃文件 10.5.1 的取捨說明）：這裡只負責
「螢幕截圖 → 文字描述」，不負責生成角色會怎麼講、也不負責決定要不要主動開口，
那些交給呼叫端（desktop_pet.py）用現有的 chat_ja + persona.md 處理，角色的
語氣/人設才會維持一致，不會因為換了看圖模型就講話變調。

隱私設計（見規劃文件 10.5.3）：擷取螢幕前先檢查目前作用中視窗的標題／程式
名稱，命中黑名單就直接跳過，連截圖都不產生——不做「AI 判斷這張圖敏不敏感」，
因為連 Microsoft Recall 那種規模的團隊都做不準，純字串比對可靠得多。
"""
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import psutil
import win32gui
import win32process
from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.env import load_dotenv  # noqa: E402

load_dotenv()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
VISION_MODEL = os.environ.get("SCREEN_WATCH_VISION_MODEL", "qwen3-vl:2b")

_CAPTION_PROMPT = (
    "這是使用者目前的電腦螢幕截圖。請具體描述畫面上實際看到的內容：視窗標題、"
    "正在使用的軟體名稱、畫面上出現的文字/影片標題/遊戲畫面裡具體發生什麼事，"
    "越具體越好——不要只講「在用電腦」「在看影片」這種空泛的話，要講得出實際看到"
    "什麼字、什麼標題、什麼畫面內容。用繁體中文回答，2-3 句話。"
)

# 預設黑名單只給幾個常見類別當起點，使用者可以透過
# SCREEN_WATCH_BLOCKED_PROCESSES / SCREEN_WATCH_BLOCKED_TITLE_KEYWORDS
# （逗號分隔）自行擴充，見 .env.example
_DEFAULT_BLOCKED_PROCESSES = {
    "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe",
    "lastpass.exe", "protonpass.exe",
}
_DEFAULT_BLOCKED_TITLE_KEYWORDS = {
    "bank", "銀行", "password", "密碼",
}


def _extra_from_env(var_name: str) -> set:
    raw = os.environ.get(var_name, "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _blocked_processes() -> set:
    return _DEFAULT_BLOCKED_PROCESSES | _extra_from_env("SCREEN_WATCH_BLOCKED_PROCESSES")


def _blocked_title_keywords() -> set:
    return _DEFAULT_BLOCKED_TITLE_KEYWORDS | _extra_from_env("SCREEN_WATCH_BLOCKED_TITLE_KEYWORDS")


def _foreground_window_info() -> tuple[str, str, str]:
    """回傳 (視窗標題, 程式檔名, 視窗類別名稱)，標題/程式檔名轉小寫方便比對；
    任何一步失敗就回傳空字串，當作「查不到就不要冒險」，交給呼叫端決定要不要
    照樣繼續。"""
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) or ""
        class_name = win32gui.GetClassName(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name = psutil.Process(pid).name() if pid else ""
    except Exception:
        return "", "", ""
    return title.lower(), process_name.lower(), class_name


# 桌面本體／工作列／開始鍵這幾個「殼」視窗不算隱私考量，是另一種排除理由：
# 截了也只會拍到桌布或工作列，看圖模型只會生出「使用者在看桌面」這種空泛
# 描述，直接跳過，省一次沒意義的看圖呼叫
_SHELL_WINDOW_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Button"}


def is_capture_blocked() -> bool:
    """目前作用中的視窗是否命中黑名單（程式名稱、標題關鍵字，或桌面/工作列殼
    視窗）。查不到視窗資訊（API 呼叫失敗）也視為封鎖，寧可少看一次，不要在
    不確定的情況下硬拍。"""
    title, process_name, class_name = _foreground_window_info()
    if not title and not process_name:
        return True
    if class_name in _SHELL_WINDOW_CLASSES:
        return True
    if process_name in _blocked_processes():
        return True
    return any(keyword in title for keyword in _blocked_title_keywords())


def _capture_screenshot_png_bytes() -> bytes:
    img = ImageGrab.grab()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class ScreenCaptureBlockedError(RuntimeError):
    pass


def _caption_image(image_bytes: bytes) -> str:
    img_b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "user", "content": _CAPTION_PROMPT, "images": [img_b64]},
        ],
        "stream": False,
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as e:
        raise RuntimeError(f"連不上本地 Ollama 服務，看圖失敗：{e}") from e
    return data["message"]["content"].strip()


def look_at_screen() -> Optional[str]:
    """看一眼螢幕、回傳文字描述；命中排除清單或任何一步失敗都回傳 None，
    呼叫端看到 None 就當這次沒看、跳過這輪，不用特別處理錯誤訊息。"""
    if is_capture_blocked():
        return None
    try:
        image_bytes = _capture_screenshot_png_bytes()
        return _caption_image(image_bytes)
    except Exception as e:
        print(f"[screen_watch] 看螢幕失敗：{e}")
        return None
