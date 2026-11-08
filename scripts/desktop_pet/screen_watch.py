"""螢幕讀取功能。

讓桌寵能偶爾看一眼使用者的螢幕、把畫面轉成一句文字描述，提供給
desktop_pet.py 裡「背景主動搭話」的迴圈使用。完整規劃詳見
TTS通用平台_專案規劃.md 第 10.5 節。

## 為什麼「看圖」跟「平常對話」要分成兩個模型

（詳細取捨說明見規劃文件 10.5.1 節）

這支模組只負責「螢幕截圖 -> 文字描述」這一步，不負責生成角色會怎麼
講這段描述、也不負責決定「現在要不要主動開口跟使用者說話」。

這些後續的判斷，全部交給呼叫端（desktop_pet.py）用既有的
chat_ja + persona.md 那一套邏輯去處理。這樣角色的語氣與人設才能
維持一致，不會因為換了一個看圖用的視覺模型，講出來的話風格就跟著
跑掉——看圖模型只負責「看懂畫面」，角色的「嘴」還是同一張嘴。

## 隱私設計

（詳細說明見規劃文件 10.5.3 節）

在真正擷取螢幕畫面之前，會先檢查目前作用中視窗的標題／程式名稱，
只要命中黑名單，就直接跳過整個流程，連截圖這個動作本身都不會發生。

這裡刻意不做「讓 AI 自己判斷這張截圖敏不敏感」這種設計，因為就連
Microsoft Recall 那種規模的團隊都做不準這件事，用單純的字串比對
黑名單反而可靠得多、也更容易讓使用者理解與信任這個機制。
"""

import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np
import psutil
import win32gui
import win32process
from PIL import Image, ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.env import load_dotenv  # noqa: E402

load_dotenv()


# ============================================================
# 環境變數 / 設定值
# ============================================================

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
VISION_MODEL = os.environ.get("SCREEN_WATCH_VISION_MODEL", "qwen3-vl:2b")
SCREENSHOT_MAX_SIDE = int(os.environ.get("SCREEN_WATCH_MAX_SIDE", "1280"))  # 截圖縮到長邊幾像素再給模型看
VISION_NUM_CTX = int(os.environ.get("SCREEN_WATCH_NUM_CTX", "4096"))
VISION_KEEP_ALIVE = os.environ.get("SCREEN_WATCH_KEEP_ALIVE", "60s")  # 看圖模型閒置多久就從顯示卡卸載


def unload_models(models: list[str]) -> None:
    """叫 Ollama 立刻卸載這些模型（桌寵關閉時用），不用等它閒置 5 分鐘。Ollama 沒開就算了。"""
    url = OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/generate"
    for model in models:
        try:
            req = urllib.request.Request(url, json.dumps({"model": model, "keep_alive": 0}).encode("utf-8"),
                                         {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass

_CAPTION_PROMPT = (
    "這是使用者目前的電腦螢幕截圖。請具體描述畫面上實際看到的內容：視窗標題、"
    "正在使用的軟體名稱、畫面上出現的文字/影片標題/遊戲畫面裡具體發生什麼事，"
    "越具體越好——不要只講「在用電腦」「在看影片」這種空泛的話，要講得出實際看到"
    "什麼字、什麼標題、什麼畫面內容。用繁體中文回答，1-2 句話，60 字以內。"
)
# 描述的長度上限（token）。看一次螢幕的時間大部分花在模型寫描述，描述越長佔顯示卡越久；
# 提示詞要求 60 字內，這裡再設硬上限，模型不聽話時也不會寫太長。
CAPTION_MAX_TOKENS = int(os.environ.get("SCREEN_WATCH_CAPTION_MAX_TOKENS", "160"))
# 畫面沒變就沿用上一次的描述，不再叫視覺模型。比較方式：縮成 48x27 灰階，
# 每個像素平均差異低於這個值（0～255）就算沒變。滑鼠移動、時鐘跳動這種小變化會被忽略。
SCREEN_CHANGE_THRESHOLD = float(os.environ.get("SCREEN_WATCH_CHANGE_THRESHOLD", "4"))
# 同一個視窗停多久（秒）要重看一次。視窗、標題一變就會馬上看，不受這個限制。
# 平常 45 秒，跟看螢幕的間隔一樣（等於每輪都看，但畫面沒變還是沿用描述）；右鍵選單開「遊戲模式」時改用 GAME 那個，
# 遊戲畫面一直在動、比對縮圖省不到，只能靠拉長間隔讓出顯示卡。
SCREEN_RELOOK_SEC = float(os.environ.get("SCREEN_WATCH_RELOOK_SEC", "45"))
SCREEN_RELOOK_GAME_SEC = float(os.environ.get("SCREEN_WATCH_RELOOK_GAME_SEC", "120"))
game_mode = False  # 由桌寵的右鍵選單切換

# 預設黑名單只涵蓋幾個常見類別當作起點。使用者可以透過環境變數
# SCREEN_WATCH_BLOCKED_PROCESSES / SCREEN_WATCH_BLOCKED_TITLE_KEYWORDS
# （逗號分隔多個值）自行擴充，詳見 .env.example。
_DEFAULT_BLOCKED_PROCESSES = {
    "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe",
    "lastpass.exe", "protonpass.exe",
}
_DEFAULT_BLOCKED_TITLE_KEYWORDS = {
    "bank", "銀行", "password", "密碼",
}


def _extra_from_env(var_name: str) -> set:
    """把環境變數裡逗號分隔的字串，轉成小寫、去除空白的集合。"""
    raw = os.environ.get(var_name, "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _blocked_processes() -> set:
    """回傳目前生效的完整程式黑名單（預設清單 + 使用者自訂擴充）。"""
    return _DEFAULT_BLOCKED_PROCESSES | _extra_from_env("SCREEN_WATCH_BLOCKED_PROCESSES")


def _blocked_title_keywords() -> set:
    """回傳目前生效的完整標題關鍵字黑名單（預設清單 + 使用者自訂擴充）。"""
    return _DEFAULT_BLOCKED_TITLE_KEYWORDS | _extra_from_env("SCREEN_WATCH_BLOCKED_TITLE_KEYWORDS")


# ============================================================
# 目前作用中視窗的資訊查詢
# ============================================================


def _foreground_window_info() -> tuple[str, str, str]:
    """查詢目前作用中視窗的資訊。

    回傳 (視窗標題, 程式檔名, 視窗類別名稱) 這個 tuple，其中標題與
    程式檔名都會轉成小寫，方便後續跟黑名單做不分大小寫的比對。

    只要任何一個步驟查詢失敗（例如 API 呼叫出錯、找不到對應行程），
    就整組回傳空字串——這是刻意採取「查不到資訊就當作有風險」的
    保守策略，把「要不要冒險繼續」的決定權交給呼叫端。
    """
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) or ""
        class_name = win32gui.GetClassName(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name = psutil.Process(pid).name() if pid else ""
    except Exception:
        return "", "", ""
    return title.lower(), process_name.lower(), class_name


# 桌面本體／工作列／開始鍵這幾個「殼」視窗，被排除的理由跟隱私無關，
# 是另一種考量：就算截了圖，也只會拍到桌布或工作列這種畫面，
# 看圖模型只會生出「使用者在看桌面」這種空泛描述，直接跳過可以省下
# 一次沒有意義的看圖呼叫（連帶省下對應的運算資源與延遲）。
_SHELL_WINDOW_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Button"}


def is_capture_blocked() -> bool:
    """判斷目前作用中的視窗是否應該被排除、不進行螢幕擷取。

    命中以下任何一種情況，就會回傳 True（代表這次應該跳過）：
    - 完全查不到視窗資訊（標題、程式名稱都是空字串）
    - 是桌面本體／工作列／開始鍵這類「殼」視窗
    - 程式檔名命中程式黑名單
    - 視窗標題包含黑名單裡的關鍵字
    """
    title, process_name, class_name = _foreground_window_info()

    if not title and not process_name:
        return True
    if class_name in _SHELL_WINDOW_CLASSES:
        return True
    if process_name in _blocked_processes():
        return True

    return any(keyword in title for keyword in _blocked_title_keywords())


# ============================================================
# 擷取螢幕 + 呼叫視覺模型生成描述
# ============================================================


def _capture_screenshot_png_bytes() -> bytes:
    """擷取目前整個螢幕畫面，縮小後回傳 PNG 格式的位元組資料。

    縮小是為了效能：視覺模型的計算量跟圖片大小成正比，原尺寸（1920x1080）
    一次要算 20 幾秒，又每隔幾十秒就看一次，開遊戲時會一直跟遊戲搶顯示卡。
    長邊 1280 還看得出畫面在做什麼。
    """
    img = ImageGrab.grab()
    img.thumbnail((SCREENSHOT_MAX_SIDE, SCREENSHOT_MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class ScreenCaptureBlockedError(RuntimeError):
    """呼叫端想要在「這次擷取被隱私規則擋下」時，可以選擇拋出的例外類別。"""


def _vision_options() -> dict:
    """思考版模型（例如 qwen3-vl:2b）的思考也算在 num_predict 裡，設上限會被思考吃光、回傳空白，
    所以只有非思考版（名稱有 instruct）才加長度上限。"""
    options = {"num_ctx": VISION_NUM_CTX}
    if "instruct" in VISION_MODEL:
        options["num_predict"] = CAPTION_MAX_TOKENS
    return options


def _caption_image(image_bytes: bytes) -> str:
    """把截圖丟給本地的視覺語言模型，取得一段文字描述。"""
    img_b64 = base64.b64encode(image_bytes).decode()

    payload = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "user", "content": _CAPTION_PROMPT, "images": [img_b64]},
        ],
        "stream": False,
        # 沒指定的話 Ollama 用模型預設的 32768，2B 的模型會佔到 5.4 GB 顯示記憶體；
        # 一張縮小後的截圖加提示詞用不到這麼多
        "options": _vision_options(),
        "think": False,  # 有支援的模型會關掉思考；qwen3-vl:2b（thinking 版）不吃這個參數
        # Ollama 預設模型閒置 5 分鐘才卸載。平常 45 秒看一次，1 分鐘內會再用到，所以一直留著；
        # 遊戲模式 2 分鐘才看一次，中間就會卸載，把顯示記憶體讓給遊戲
        "keep_alive": VISION_KEEP_ALIVE,
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


_last_skipped_window = None


def look_at_screen() -> Optional[str]:
    """看一眼螢幕，回傳文字描述。

    只要命中隱私排除清單、或過程中任何一步發生錯誤，都會回傳 None——
    呼叫端看到 None 時，就當作「這次沒看成」直接跳過這一輪即可，
    不需要另外處理任何錯誤訊息或例外狀況。
    """
    global _last_skipped_window
    if is_capture_blocked():
        # 同一個視窗只印一次，不然停在桌面時每 20 秒都會洗一行。
        title, process_name, class_name = _foreground_window_info()
        if (process_name, title) != _last_skipped_window:
            _last_skipped_window = (process_name, title)
            print(f"[screen_watch] 這個視窗不看：{process_name or '未知'}｜{class_name}｜{title}")
        return None
    _last_skipped_window = None

    # 什麼時候真的叫視覺模型看圖（參考 Open-LLM-VTuber「需要時才看」的做法，省顯示卡）：
    # 1. 換了視窗、或視窗標題變了（換分頁、換影片）→ 馬上看
    # 2. 同一個視窗停超過 SCREEN_RELOOK_SEC（遊戲模式用 SCREEN_RELOOK_GAME_SEC）→ 再看一次
    # 3. 其他時候沿用上一次的描述。角色照樣講話，只是省掉看圖這一步
    # 第 2 點時如果畫面幾乎沒變（比對縮圖），也沿用，只重設計時
    # 呼叫端可以從 last_look 知道這次是「真的看了」還是「沿用」，以及目前是哪個視窗／程式：
    # 沿用的話要提醒角色換話題，不然遊戲模式下 2 分鐘才看一次，中間每 20 秒都拿到同一段描述、一直講同一句。
    global _last_signature, _last_caption, _last_window, _last_look, last_look
    try:
        title, process_name, _ = _foreground_window_info()
        window = (process_name, title)
        now = time.monotonic()
        same_window = _last_caption and window == _last_window
        relook = SCREEN_RELOOK_GAME_SEC if game_mode else SCREEN_RELOOK_SEC
        last_look = {"fresh": False, "title": _foreground_title(), "process": process_name}
        if same_window and now - _last_look < relook:
            return _last_caption

        image_bytes = _capture_screenshot_png_bytes()
        signature = _screen_signature(image_bytes)
        if (same_window and _last_signature is not None
                and float(np.abs(signature - _last_signature).mean()) < SCREEN_CHANGE_THRESHOLD):
            _last_look = now
            return _last_caption
        caption = _caption_image(image_bytes)
        _last_signature, _last_caption, _last_window, _last_look = signature, caption, window, now
        last_look["fresh"] = True
        return caption
    except Exception as e:
        print(f"[screen_watch] 看螢幕失敗：{e}")
        return None


_last_signature = None
_last_caption = None
_last_window = None
_last_look = 0.0
last_look = {"fresh": False, "title": "", "process": ""}


def _foreground_title() -> str:
    """前景視窗的原始標題（不轉小寫），讓角色認得出是哪個遊戲、哪個網站。"""
    try:
        return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
    except Exception:
        return ""


def _screen_signature(image_bytes: bytes) -> "np.ndarray":
    """把截圖縮成很小的灰階圖，用來快速判斷畫面有沒有變。"""
    small = Image.open(io.BytesIO(image_bytes)).convert("L").resize((48, 27), Image.BILINEAR)
    return np.asarray(small, dtype=np.float32)
