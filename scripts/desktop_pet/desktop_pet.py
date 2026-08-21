"""
桌寵前端（無邊框透明視窗版）：畫面上只有角色本人在桌面上飄著，沒有標題列、
沒有面板方塊。打字、講話 → LLM 生成回覆 → TTS 生成語音 → 同時播給你聽
（系統目前的預設輸出裝置）、也播給 VTube Studio 的虛擬音效卡（CABLE
Input），讓它對嘴；桌寵本體的立繪也會依音量開合嘴。

互動方式：
    - 點一下角色：彈出一個貼在角色下方的極簡輸入列，打完按 Enter 送出、
      Esc 取消、點掉焦點自動收起——平常沒有這個輸入列，不會擋畫面
    - 按住角色拖曳：移動視窗位置（跟點一下是用有沒有移動位置來分辨，不是
      分開的操作）
    - 按住 F9 講話、放開結束：全域熱鍵，轉出來的文字直接送出（見下方
      _on_ptt_transcribed 的取捨說明）
    - 右鍵角色：彈出選單（顯示/隱藏歷史紀錄、結束）——沒有標題列就沒有
      關閉鈕，結束程式要走這裡
    - 重力/落地（見 desktop_physics.py）：放著不管會自己往下掉，落在工作列
      或其他視窗的上緣，不會呆呆飄在螢幕中間；右鍵選單可以隨時關掉這個效果

只有這裡生成出來的語音會進虛擬音效卡——是程式直接指定裝置播放，不是把系統
預設輸出整個換成虛擬音效卡，所以不會有其他系統音效（通知音、其他程式聲音）
也跟著驅動嘴型的問題。

語音輸入是 push-to-talk：按住熱鍵錄音、放開後轉文字**直接送出**（不會先
給你看過再按 Enter）。這點跟 Discord 版語音輸入被擱置的原因（聽錯了會直接
餵給 LLM，沒有確認機會）看似矛盾，但這裡的輸入列本來就是「點出來即時打字」
的定位，語音轉錯字重講一次成本很低，跟 Discord 那種需要跨裝置確認的情境
不同——如果之後覺得還是想要「轉完先看一眼」，把 _on_ptt_transcribed 裡
直接呼叫 _submit() 的地方改成塞進彈出輸入列、不自動送出就好。

視窗透明用的是 Windows 專屬的 -transparentcolor 色鍵去背技巧：整張立繪先
被貼到一張洋紅色（不會出現在角色圖裡的顏色）背景上，再把那個顏色設成整個
視窗的透明色——缺點是邊緣是硬邊（二值化 alpha），沒有平滑抗鋸齒，但比另外
接 win32 API 做逐像素 alpha 混合的視窗簡單很多，先求有再求好。

前置需求：
    - Windows 的預設播放裝置要設回你平常聽的裝置（不要是 CABLE Input）——
      這裡改由程式碼指定要不要餵給虛擬音效卡，不吃系統預設值
    - VB-Audio Virtual Cable 已安裝
    - VTube Studio 已開啟、載入模型，「進階唇音同步」的麥克風來源設定成
      CABLE Output，且 Mouth Open 的輸入參數是 VoiceVolumePlusMouthOpen
      （不是 MouthOpen，那個只吃攝影機追蹤，見規劃文件 10.4）
    - 本地 Ollama、GPT-SoVITS 推理服務（會自動偵測/啟動，邏輯沿用
      tts_server.py）
    - 語音輸入用 SenseVoice（見 stt.py），第一次按熱鍵才會載入模型
    - 立繪素材（mouth_open.png / mouth_closed.png，各自是完整的角色圖，不是
      疊圖用的局部素材）放在 characters/sakiko/avatar/ 底下，需要 Pillow（PIL）

用法：
    C:\\miniconda3\\envs\\tts-platform\\python.exe scripts\\desktop_pet\\desktop_pet.py
    按住 F9 講話、放開結束（熱鍵是全域的，切到遊戲畫面也能用，不用先切回這個視窗）
"""
import ctypes
import json
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
from PIL import Image, ImageTk
from pynput import keyboard as pynput_keyboard

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gpt_sovits"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "llm"))

import chat  # noqa: E402  — 這裡特意連模組本身一起 import（不是只挑函式），
# 右鍵選單切換本地/雲端時要直接改 chat.LLM_PROVIDER 這個模組層級變數，
# chat_ja/translate_zh 每次呼叫都讀當下的值，不用重開程式就能生效
from chat import chat_ja, translate_zh  # noqa: E402
from tts_infer import generate  # noqa: E402
from tts_server import is_server_ready, start_server  # noqa: E402
import stt  # noqa: E402  — 同上，切換 STT 本地/雲端要改 stt.STT_PROVIDER
from stt import PushToTalkRecorder  # noqa: E402
from screen_watch import look_at_screen  # noqa: E402
import desktop_physics  # noqa: E402

# Ave Mujica 氛圍配色（哥德紫＋金色點綴）——不是查證過的官方角色色票，
# 是照劇中面具/薔薇/歌德風美術方向抓的氣氛色，想換成別的顏色跟我說就好
COLOR_BG = "#15101c"
COLOR_PANEL = "#241a30"
COLOR_ACCENT = "#7c4dbd"
COLOR_ACCENT_HOVER = "#5e3690"
COLOR_GOLD = "#c9a86a"
COLOR_TEXT = "#eee6f5"
COLOR_STATUS_TEXT = "#b8a8c8"  # 狀態文字（錄音中／生成中）用比正式台詞暗一點的顏色分開

MAGIC_TRANSPARENT_COLOR = "#ff00ff"  # 視窗去背色鍵，角色圖裡不會出現這個顏色

PUSH_TO_TALK_KEY = pynput_keyboard.Key.f9  # 全域熱鍵，按住講話、放開結束
PUSH_TO_TALK_KEY_LABEL = "F9"

CABLE_DEVICE_NAME_HINT = "CABLE Input"

# 立繪素材放在 characters/<角色>/avatar/，跟角色人設、語音設定同一套規則；
# 要換角色，把這裡的 "sakiko" 換成你自己的角色資料夾名稱
ASSETS_DIR = Path(__file__).resolve().parents[2] / "characters" / "sakiko" / "avatar"
AVATAR_DISPLAY_WIDTH = int(os.environ.get("DESKTOP_PET_AVATAR_WIDTH", "280"))  # 立繪顯示寬度（像素）預設值，高度依素材比例算出來；可用滑鼠滾輪即時縮放（見 _on_avatar_scroll），縮放後會記住，優先於這個預設值
AVATAR_MIN_WIDTH = 120
AVATAR_MAX_WIDTH = 600
AVATAR_SCROLL_STEP = 20  # 滾輪每格縮放的像素量
_AVATAR_RESIZE_DEBOUNCE_MS = 60  # 滾輪停下來這麼久才真的重繪，避免連續滾動時每格都重繪造成卡頓

# 角色說話語言的常見選項，右鍵選單快速切換用；不在清單裡的語言走選單裡的
# 「自訂...」，不是只能從這幾個裡面選
SPEECH_LANGUAGE_PRESETS = ["日文", "中文", "英文", "韓文"]
MOUTH_HOP_MS = 90  # 音量驅動開合嘴的取樣間隔，不是真的音素對齊
MOUTH_THRESHOLD_RATIO = 0.3  # 這段音量超過整句最大音量的這個比例才算「張嘴」
DRAG_THRESHOLD_PX = 3  # 按下去移動超過這個距離才算拖曳，不然當成單純點擊

# 桌寵物理（重力／落地，見 desktop_physics.py）：純手感調出來的值，不是物理
# 正確的單位換算，調快/調慢直接改這兩個數字就好
PHYSICS_TICK_MS = 33  # 重力/落地判定的更新頻率，約 30fps
GRAVITY_PX_S2 = 2200.0
FALL_TERMINAL_VELOCITY_PX_S = 1400.0

# 落地後的自主漫遊：左右滑動現有靜態立繪（翻轉表示方向），不是真的走路動畫，
# 純手感調出來的值
WANDER_SPEED_PX_S = 110.0
WANDER_MIN_INTERVAL_SEC = 5.0  # 兩次漫遊之間至少等這麼久
WANDER_MAX_INTERVAL_SEC = 15.0
WANDER_MIN_DURATION_SEC = 1.0  # 一次漫遊走多久
WANDER_MAX_DURATION_SEC = 3.0

# 螢幕讀取主動搭話（規劃見 TTS通用平台_專案規劃.md 10.5）：開關不是靠改 .env
# 這種要開檔案編輯的方式決定（試用門檻太高，等於變相勸退），而是程式啟動時
# 跳出詢問視窗、加上右鍵選單隨時可切換——「記不記住這次選擇」才是使用者
# 真正要做的決定。同一個理由，對話/語音辨識要走本地還是雲端，也一併做成
# 右鍵選單可以直接切換，不用進 .env 改——記住的偏好統一存進下面這個本機
# 小檔案，不記住的話下次開程式還是照 .env 的預設值
SCREEN_WATCH_INTERVAL_SEC = int(os.environ.get("SCREEN_WATCH_INTERVAL_SEC", "20"))
_PET_PREFS_FILE = Path(__file__).resolve().parent / ".pet_prefs.json"

_GA_ROOT = 2  # win32 GetAncestor 旗標：取得最上層視窗
_WDA_EXCLUDEFROMCAPTURE = 0x00000011  # win32 SetWindowDisplayAffinity 旗標


def _exclude_self_from_screen_capture(tk_hwnd: int) -> int:
    """讓 screen_watch 的全螢幕擷取跳過桌寵本體視窗，不然看螢幕功能會把自己
    的立繪也拍進去、餵給看圖模型。pywin32 沒包這個 API，直接用 ctypes 呼叫
    user32；失敗（例如舊版 Windows 不支援）就當作沒這回事，不影響其他功能。
    回傳解析出來的最上層 hwnd（失敗就回傳原本的 tk_hwnd），讓呼叫端可以重用
    這個 hwnd 做其他 win32 呼叫（見 desktop_physics，落地判定要排除自己）。"""
    try:
        user32 = ctypes.windll.user32
        root_hwnd = user32.GetAncestor(tk_hwnd, _GA_ROOT) or tk_hwnd
        user32.SetWindowDisplayAffinity(root_hwnd, _WDA_EXCLUDEFROMCAPTURE)
        return root_hwnd
    except Exception:
        return tk_hwnd
_SCREEN_WATCH_PROMPT_TEMPLATE = (
    "（你不經意瞄了一眼主人的螢幕，看到的畫面大概是：{caption}。"
    "用你平常的口吻主動跟主人講一句話，針對畫面裡具體的東西（例如提到的標題、"
    "遊戲名稱、正在做的事）給出有內容的反應、關心或吐槽，不要生硬地複述看到的內容，"
    "也不要提到「螢幕」「截圖」這類字眼——就自然地像是瞄了一眼隨口說一句話。"
    "**絕對不要講「需要幫忙嗎」「有什麼可以幫你的」這種制式客套話**，沒有具體東西"
    "好反應的話寧可換個角度吐槽或關心，也不要落入這種空話。）"
)


def _load_prefs() -> dict:
    """讀取記住的本機偏好（螢幕讀取開關、對話/STT 走本地或雲端）；檔案不存在
    或壞掉就回傳空字典，呼叫端看某個 key 不存在時要用 .env 的預設值，不能
    自己瞎猜。"""
    if not _PET_PREFS_FILE.exists():
        return {}
    try:
        return json.loads(_PET_PREFS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_prefs(**updates) -> None:
    prefs = _load_prefs()
    prefs.update(updates)
    _PET_PREFS_FILE.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")


_avatar_source_cache: Optional[tuple] = None  # (closed_cropped, opened_cropped, 高寬比)，見 _load_avatar_source


def _load_avatar_source() -> tuple:
    """讀 assets 底下兩張完整的角色立繪、算裁切框，只做一次並快取——這步驟
    含磁碟讀取＋在原始解析度上算透明範圍，比較花時間，但跟縮放到多寬完全
    無關，滾輪縮放時重複做這步只是浪費，快取起來才不會每滾一格就卡一下。"""
    global _avatar_source_cache
    if _avatar_source_cache is not None:
        return _avatar_source_cache

    closed = Image.open(ASSETS_DIR / "mouth_closed.png").convert("RGBA")
    opened = Image.open(ASSETS_DIR / "mouth_open.png").convert("RGBA")

    def _content_bbox(img: Image.Image):
        return img.split()[-1].point(lambda a: 255 if a > 200 else 0).getbbox()

    # 兩張圖各自生成，不透明範圍可能有些微差異（頭髮姿態不會完全一致），這裡
    # 用兩張的聯集算同一個裁切框，兩張圖套用同一個框，避免切換嘴型時因為裁切
    # 位置不同讓角色看起來跳動
    cx0, cy0, cx1, cy1 = _content_bbox(closed)
    ox0, oy0, ox1, oy1 = _content_bbox(opened)
    x0, y0 = min(cx0, ox0), min(cy0, oy0)
    x1, y1 = max(cx1, ox1), max(cy1, oy1)
    pad = int((x1 - x0) * 0.15)
    left = max(0, x0 - pad)
    right = min(closed.width, x1 + pad)
    box = (left, 0, right, closed.height)

    closed_cropped = closed.crop(box)
    opened_cropped = opened.crop(box)
    aspect_ratio = closed_cropped.height / closed_cropped.width
    _avatar_source_cache = (closed_cropped, opened_cropped, aspect_ratio)
    return _avatar_source_cache


def _load_avatar_frames(display_width: int = AVATAR_DISPLAY_WIDTH, mirror: bool = False):
    """把已經裁切好的立繪（見 _load_avatar_source）縮放成面板要顯示的寬度。
    嘴巴位置/大小完全由畫圖那邊決定，程式不再自動裁切疊圖。mirror=True 時
    左右翻轉——漫遊往左走時用，省了另外準備一組「面向左」立繪素材的成本。
    回傳 (closed_photo, open_photo, display_height)——PhotoImage 物件呼叫端
    要留住參照，不然會被 GC 掉、畫面變空白。"""
    closed_cropped, opened_cropped, aspect_ratio = _load_avatar_source()
    if mirror:
        closed_cropped = closed_cropped.transpose(Image.FLIP_LEFT_RIGHT)
        opened_cropped = opened_cropped.transpose(Image.FLIP_LEFT_RIGHT)
    display_height = round(display_width * aspect_ratio)

    def _to_photo(img: Image.Image) -> ImageTk.PhotoImage:
        resized = img.resize((display_width, display_height), Image.LANCZOS)
        # 色鍵去背只能整格全透明或全不透明，不吃漸層 alpha，先二值化才不會
        # 在邊緣留下一圈色鍵色的毛邊（代價是邊緣是硬邊，沒有平滑抗鋸齒）
        alpha = resized.split()[-1].point(lambda a: 255 if a > 128 else 0)
        canvas = Image.new("RGB", resized.size, MAGIC_TRANSPARENT_COLOR)
        canvas.paste(resized.convert("RGB"), mask=alpha)
        return ImageTk.PhotoImage(canvas)

    return _to_photo(closed_cropped), _to_photo(opened_cropped), display_height


def _mouth_envelope(data: np.ndarray, samplerate: int, hop_ms: int = MOUTH_HOP_MS) -> list:
    """把音檔切成一段一段（hop_ms 一段），算每段音量，回傳每段「嘴巴該不該
    張開」的 bool list。只是音量夠大就張嘴的陽春版本，不是真的音素對齊
    （對齊需要 TTS 吐出逐字時間戳，見規劃文件 10.4 的討論）。"""
    mono = data.mean(axis=1) if data.ndim > 1 else data
    hop = max(1, int(samplerate * hop_ms / 1000))
    levels = [
        float(np.sqrt(np.mean(mono[i:i + hop] ** 2)))
        for i in range(0, len(mono), hop)
    ]
    peak = max(levels) if levels else 0.0
    if peak <= 1e-6:
        return [False] * len(levels)
    return [level > peak * MOUTH_THRESHOLD_RATIO for level in levels]


def _find_cable_input_device() -> Optional[int]:
    """找虛擬音效卡的播放端。同名裝置在不同 host API（MME/DirectSound/WASAPI）
    下會重複出現好幾筆，優先挑跟 sounddevice 預設 host API 一致的那筆，
    行為比較穩定；都沒有就隨便挑第一筆。"""
    devices = sd.query_devices()
    default_hostapi = sd.default.hostapi
    candidates = [
        i
        for i, d in enumerate(devices)
        if CABLE_DEVICE_NAME_HINT in d["name"] and d["max_output_channels"] > 0
    ]
    for i in candidates:
        if devices[i]["hostapi"] == default_hostapi:
            return i
    return candidates[0] if candidates else None


def _play_on_device(data: np.ndarray, samplerate: int, device_index: int) -> None:
    channels = data.shape[1] if data.ndim > 1 else 1
    with sd.OutputStream(device=device_index, samplerate=samplerate, channels=channels) as stream:
        stream.write(data.astype(np.float32))


def play_for_avatar(data: np.ndarray, samplerate: int, on_mouth=None) -> None:
    """播放生成的語音：一份給你（系統目前的預設輸出裝置）聽，一份指定播給
    虛擬音效卡（VTube Studio 對嘴用），兩份同時播放。如果有給 on_mouth
    callback（簽名 (is_open: bool) -> None），會依音量節奏在另一條執行緒上
    呼叫它，讓桌寵視窗裡的立繪跟著開合嘴——跟播放本身是獨立算的，兩邊都跑
    完這個函式才返回。"""
    targets: list[int] = []
    default_output = sd.default.device[1]
    if default_output is not None:
        targets.append(default_output)

    cable_index = _find_cable_input_device()
    if cable_index is None:
        print("[desktop_pet] 找不到 CABLE Input 裝置，只會播給你聽，VTube Studio 不會對嘴")
    elif cable_index != default_output:
        targets.append(cable_index)

    threads = [threading.Thread(target=_play_on_device, args=(data, samplerate, idx)) for idx in targets]

    mouth_thread = None
    if on_mouth is not None:
        envelope = _mouth_envelope(data, samplerate)
        hop_seconds = MOUTH_HOP_MS / 1000

        def _run_mouth_clock():
            start = time.monotonic()
            for i, is_open in enumerate(envelope):
                delay = start + i * hop_seconds - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                on_mouth(is_open)
            on_mouth(False)

        mouth_thread = threading.Thread(target=_run_mouth_clock)

    for t in threads:
        t.start()
    if mouth_thread:
        mouth_thread.start()
    for t in threads:
        t.join()
    if mouth_thread:
        mouth_thread.join()


class CharacterWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._busy = False
        self._dragged = False
        self._drag_origin = (0, 0)
        self._win_origin = (0, 0)
        self._last_reply_text = ""
        self._history_text = ""
        self._history_window: Optional[tk.Toplevel] = None
        self._history_text_widget: Optional[tk.Text] = None
        self._input_popup: Optional[tk.Toplevel] = None
        self._input_entry: Optional[tk.Entry] = None

        prefs = _load_prefs()

        # 立繪寬度：有滾輪縮放記住的偏好就用那個，沒有就用 .env 的預設值
        self._avatar_width = prefs.get("avatar_width", AVATAR_DISPLAY_WIDTH)
        self._pending_avatar_width = self._avatar_width
        self._avatar_resize_job: Optional[str] = None
        self._facing = "right"
        self.frame_closed, self.frame_open, avatar_height = _load_avatar_frames(self._avatar_width)
        self.frame_closed_l, self.frame_open_l, _ = _load_avatar_frames(self._avatar_width, mirror=True)

        # 無邊框、置頂、色鍵去背——畫面上真的只剩角色本人
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=MAGIC_TRANSPARENT_COLOR)
        root.wm_attributes("-transparentcolor", MAGIC_TRANSPARENT_COLOR)
        root.geometry(f"{self._avatar_width}x{avatar_height}+120+120")
        root.update_idletasks()
        self._hwnd = _exclude_self_from_screen_capture(root.winfo_id())

        # 桌寵物理（重力/落地，見 desktop_physics.py）：先都當作沒按著滑鼠、
        # 沒有垂直速度，開機那一刻就會開始往下掉，直到落在某個平台或工作列
        self._mouse_down = False
        self._fall_velocity = 0.0
        self._physics_enabled_var = tk.BooleanVar(value=True)
        self._walk_seconds_left = 0.0
        self._next_wander_at = time.monotonic() + random.uniform(WANDER_MIN_INTERVAL_SEC, WANDER_MAX_INTERVAL_SEC)

        self.avatar_label = tk.Label(
            root, image=self.frame_closed, bg=MAGIC_TRANSPARENT_COLOR, bd=0, highlightthickness=0,
        )
        self.avatar_label.place(x=0, y=0, relwidth=1, relheight=1)

        self.dialogue_var = tk.StringVar(value="")
        self.dialogue_label = tk.Label(
            root, textvariable=self.dialogue_var, wraplength=self._avatar_width - 24,
            justify="left", font=("Microsoft JhengHei", 12), fg=COLOR_TEXT, bg=COLOR_PANEL,
            padx=10, pady=6,
        )
        self._update_dialogue_visibility()

        # 螢幕讀取主動搭話：有記住的偏好就直接用，沒有的話先當關閉、等視窗
        # 顯示穩定後再跳出詢問視窗（見 _prompt_screen_watch_choice）
        remembered = prefs.get("screen_watch_enabled")
        self._screen_watch_enabled = remembered if remembered is not None else False
        self._remember_screen_watch = remembered is not None
        self._screen_watch_var = tk.BooleanVar(value=self._screen_watch_enabled)

        # 對話／語音辨識走本地或雲端：記住的偏好覆蓋 chat.py／stt.py 原本從
        # .env 讀到的值（模組層級變數，直接改就會生效，見上面 import 的註解）
        if "llm_provider" in prefs:
            chat.LLM_PROVIDER = prefs["llm_provider"]
        if "stt_provider" in prefs:
            stt.STT_PROVIDER = prefs["stt_provider"]
        if "character_speech_language" in prefs:
            chat.CHARACTER_SPEECH_LANGUAGE = prefs["character_speech_language"]
        self._llm_provider_var = tk.StringVar(value=chat.LLM_PROVIDER)
        self._stt_provider_var = tk.StringVar(value=stt.STT_PROVIDER)

        self._context_menu = tk.Menu(
            root, tearoff=False, bg=COLOR_PANEL, fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT, activeforeground=COLOR_TEXT,
        )
        self._context_menu.add_command(label="顯示歷史紀錄", command=self._toggle_history_window)
        self._context_menu.add_checkbutton(
            label="螢幕讀取主動搭話", variable=self._screen_watch_var,
            command=self._on_screen_watch_toggle,
        )
        # 桌寵物理（重力/落地）第一階段先試試看，不確定好不好玩、會不會卡到
        # 其他操作，所以做成可以隨時關掉，這次先不記住選擇（跟螢幕讀取那個
        # 開關不一樣），驗證過真的想要再考慮要不要記住
        self._context_menu.add_checkbutton(
            label="重力（桌面感）", variable=self._physics_enabled_var,
        )

        llm_menu = tk.Menu(
            self._context_menu, tearoff=False, bg=COLOR_PANEL, fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT, activeforeground=COLOR_TEXT,
        )
        llm_menu.add_radiobutton(
            label="本地 Ollama", variable=self._llm_provider_var, value="local",
            command=self._on_llm_provider_change,
        )
        llm_menu.add_radiobutton(
            label="雲端 OpenAI", variable=self._llm_provider_var, value="openai",
            command=self._on_llm_provider_change,
        )
        self._context_menu.add_cascade(label="對話模型", menu=llm_menu)

        stt_menu = tk.Menu(
            self._context_menu, tearoff=False, bg=COLOR_PANEL, fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT, activeforeground=COLOR_TEXT,
        )
        stt_menu.add_radiobutton(
            label="本地 SenseVoice", variable=self._stt_provider_var, value="local",
            command=self._on_stt_provider_change,
        )
        stt_menu.add_radiobutton(
            label="雲端 OpenAI Whisper", variable=self._stt_provider_var, value="openai",
            command=self._on_stt_provider_change,
        )
        self._context_menu.add_cascade(label="語音辨識", menu=stt_menu)

        self._speech_language_var = tk.StringVar(value=chat.CHARACTER_SPEECH_LANGUAGE)
        language_menu = tk.Menu(
            self._context_menu, tearoff=False, bg=COLOR_PANEL, fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT, activeforeground=COLOR_TEXT,
        )
        for lang in SPEECH_LANGUAGE_PRESETS:
            language_menu.add_radiobutton(
                label=lang, variable=self._speech_language_var, value=lang,
                command=self._on_speech_language_preset_change,
            )
        language_menu.add_separator()
        # 常見選項之外的語言（或想輸入更精確的說法）走這個，開文字輸入框
        language_menu.add_command(label="自訂...", command=self._prompt_speech_language)
        self._context_menu.add_cascade(label="角色說話語言", menu=language_menu)

        self._context_menu.add_separator()
        self._context_menu.add_command(label="結束", command=self._on_close)

        # 角色跟對話框都能點/拖/右鍵，手感一致
        for widget in (self.avatar_label, self.dialogue_label):
            widget.bind("<ButtonPress-1>", self._on_avatar_press)
            widget.bind("<B1-Motion>", self._on_avatar_motion)
            widget.bind("<ButtonRelease-1>", self._on_avatar_release)
            widget.bind("<Button-3>", self._on_avatar_right_click)
            widget.bind("<MouseWheel>", self._on_avatar_scroll)  # 滾輪縮放立繪大小

        self._task_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.root.after(50, self._poll_queue)
        self.root.after(PHYSICS_TICK_MS, self._physics_tick)

        # push-to-talk：全域熱鍵，錄音/轉文字都在背景執行緒跑，不會卡住視窗
        self._ptt_recorder = PushToTalkRecorder(
            on_transcribed=self._on_ptt_transcribed,
            on_state_change=self._on_ptt_state_change,
        )
        self._ptt_listener = pynput_keyboard.Listener(
            on_press=self._on_key_press, on_release=self._on_key_release
        )
        self._ptt_listener.start()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        threading.Thread(target=self._screen_watch_loop, daemon=True).start()
        if remembered is None:
            self.root.after(300, self._prompt_screen_watch_choice)

    # ---------- 立繪拖曳 / 點擊 ----------

    def _on_avatar_press(self, event: tk.Event) -> None:
        self._dragged = False
        self._mouse_down = True  # 按著的這段時間物理 tick 暫停，放開後從當下位置繼續掉
        self._drag_origin = (event.x_root, event.y_root)
        self._win_origin = (self.root.winfo_x(), self.root.winfo_y())

    def _on_avatar_motion(self, event: tk.Event) -> None:
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        if abs(dx) > DRAG_THRESHOLD_PX or abs(dy) > DRAG_THRESHOLD_PX:
            self._dragged = True
        self.root.geometry(f"+{self._win_origin[0] + dx}+{self._win_origin[1] + dy}")
        self._position_input_popup()

    def _on_avatar_release(self, event: tk.Event) -> None:
        self._mouse_down = False
        self._fall_velocity = 0.0  # 放開的瞬間重新開始加速掉落，不要延續拖曳前累積的速度
        if not self._dragged:
            self._toggle_input_popup()

    # ---------- 桌寵物理（重力/落地，見 desktop_physics.py） ----------

    def _physics_tick(self) -> None:
        """每 tick 都重新問一次「正下方最近的平台在哪」，不快取任何「目前站
        在哪個視窗上」的狀態——平台被關掉/移走，下一 tick 自然就會開始往下一
        個平台掉，不用另外偵測「平台消失了」。按著角色拖曳時暫停（見
        _mouse_down），放開後從放開的位置繼續掉。落地時（沒在掉落中）才會
        觸發自主漫遊，見 _advance_wander。"""
        if self._physics_enabled_var.get() and not self._mouse_down:
            dt = PHYSICS_TICK_MS / 1000
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            surface_y = desktop_physics.find_landing_y(x, x + w, y + h, self._hwnd)
            falling = y + h < surface_y - 1
            if falling:
                self._fall_velocity = min(self._fall_velocity + GRAVITY_PX_S2 * dt, FALL_TERMINAL_VELOCITY_PX_S)
                new_y = min(y + self._fall_velocity * dt, surface_y - h)
                new_x = x
            else:
                self._fall_velocity = 0.0
                new_y = surface_y - h
                new_x = self._advance_wander(x, w, dt)
            if new_x != x or new_y != y:
                self.root.geometry(f"+{int(new_x)}+{int(new_y)}")
                self._position_input_popup()
        self.root.after(PHYSICS_TICK_MS, self._physics_tick)

    def _advance_wander(self, x: int, w: int, dt: float) -> float:
        """落地時偶爾隨機左右滑動一小段，讓她感覺「活著」，不是釘死在原地——
        沿用現有靜態立繪左右翻轉表示方向（見 _current_frame），不是真的走路
        分格動畫。忙碌中（生成回覆）或輸入列開著的時候暫停倒數/移動，才不會
        聊到一半人就走掉。"""
        if self._busy or self._input_popup is not None:
            return x
        if self._walk_seconds_left > 0:
            self._walk_seconds_left -= dt
            step = WANDER_SPEED_PX_S * dt * (1 if self._facing == "right" else -1)
            screen_w = self.root.winfo_screenwidth()
            new_x = max(0, min(x + step, screen_w - w))
            if new_x == x:
                self._walk_seconds_left = 0.0  # 撞到螢幕邊界了，提早結束這次漫遊
            return new_x
        if time.monotonic() >= self._next_wander_at:
            self._start_wander()
        return x

    def _start_wander(self) -> None:
        self._facing = random.choice(("left", "right"))
        self.avatar_label.configure(image=self._current_frame(is_open=False))
        self._walk_seconds_left = random.uniform(WANDER_MIN_DURATION_SEC, WANDER_MAX_DURATION_SEC)
        self._next_wander_at = time.monotonic() + random.uniform(WANDER_MIN_INTERVAL_SEC, WANDER_MAX_INTERVAL_SEC)

    def _current_frame(self, is_open: bool):
        if self._facing == "left":
            return self.frame_open_l if is_open else self.frame_closed_l
        return self.frame_open if is_open else self.frame_closed

    def _on_avatar_right_click(self, event: tk.Event) -> None:
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def _on_avatar_scroll(self, event: tk.Event) -> None:
        """滾輪縮放立繪大小：往上滾放大、往下滾縮小，範圍限制在
        AVATAR_MIN_WIDTH~AVATAR_MAX_WIDTH。這裡只更新「目標寬度」跟排程，真正
        重繪（縮放圖片、改視窗大小）延到 _apply_avatar_resize 才做——滾很快
        時會連續觸發一堆事件，每個都立刻重繪會卡頓，改成滾動停下來一小段時間
        （_AVATAR_RESIZE_DEBOUNCE_MS）才真的畫一次，中途只更新數字很便宜。"""
        direction = 1 if event.delta > 0 else -1
        new_width = max(AVATAR_MIN_WIDTH, min(AVATAR_MAX_WIDTH, self._pending_avatar_width + direction * AVATAR_SCROLL_STEP))
        if new_width == self._pending_avatar_width:
            return
        self._pending_avatar_width = new_width
        if self._avatar_resize_job is not None:
            self.root.after_cancel(self._avatar_resize_job)
        self._avatar_resize_job = self.root.after(_AVATAR_RESIZE_DEBOUNCE_MS, self._apply_avatar_resize)

    def _apply_avatar_resize(self) -> None:
        """滾輪停下來一小段時間後才真的執行的重繪：縮放/去背圖片（快取過原始
        素材，這步不用重讀磁碟，見 _load_avatar_source）、改視窗大小、記住這次
        選擇的大小。"""
        self._avatar_resize_job = None
        if self._pending_avatar_width == self._avatar_width:
            return
        self._avatar_width = self._pending_avatar_width

        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.frame_closed, self.frame_open, avatar_height = _load_avatar_frames(self._avatar_width)
        self.frame_closed_l, self.frame_open_l, _ = _load_avatar_frames(self._avatar_width, mirror=True)
        self.avatar_label.configure(image=self._current_frame(is_open=False))
        self.root.geometry(f"{self._avatar_width}x{avatar_height}+{x}+{y}")
        self.dialogue_label.configure(wraplength=self._avatar_width - 24)
        self._position_input_popup()
        _save_prefs(avatar_width=self._avatar_width)

    # ---------- 彈出輸入列 ----------

    def _toggle_input_popup(self) -> None:
        if self._input_popup is not None:
            self._hide_input_popup()
        else:
            self._show_input_popup()

    def _show_input_popup(self) -> None:
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=COLOR_ACCENT)  # 外層當一圈細邊框

        inner = tk.Frame(popup, bg=COLOR_PANEL)
        inner.pack(padx=1, pady=1)

        entry = tk.Entry(
            inner, font=("Microsoft JhengHei", 13), bg=COLOR_PANEL, fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT, relief="flat", width=26,
        )
        entry.pack(padx=8, pady=6)
        entry.bind("<Return>", self._on_popup_submit)
        entry.bind("<Escape>", lambda e: self._hide_input_popup())
        entry.bind("<FocusOut>", lambda e: self._hide_input_popup())

        self._input_popup = popup
        self._input_entry = entry
        self._position_input_popup()
        entry.focus_force()

    def _position_input_popup(self) -> None:
        if self._input_popup is None:
            return
        self.root.update_idletasks()
        x = self.root.winfo_x()
        y = self.root.winfo_y() + self.root.winfo_height() + 4
        self._input_popup.geometry(f"+{x}+{y}")

    def _hide_input_popup(self) -> None:
        if self._input_popup is not None:
            self._input_popup.destroy()
            self._input_popup = None
            self._input_entry = None

    def _on_popup_submit(self, event: tk.Event = None) -> None:
        if self._input_entry is None:
            return
        text = self._input_entry.get().strip()
        self._hide_input_popup()
        if text:
            self._submit(text)

    # ---------- 歷史紀錄視窗（右鍵選單開，跟主視窗分開、不強求無邊框） ----------

    def _toggle_history_window(self) -> None:
        if self._history_window is not None:
            self._history_window.destroy()
            self._history_window = None
            self._history_text_widget = None
            self._context_menu.entryconfig(0, label="顯示歷史紀錄")
            return

        win = tk.Toplevel(self.root)
        win.title("歷史紀錄")
        win.configure(bg=COLOR_BG)
        win.geometry("320x400")
        win.attributes("-topmost", True)

        text = tk.Text(
            win, wrap="word", state="disabled", font=("Microsoft JhengHei", 12),
            bg=COLOR_PANEL, fg=COLOR_TEXT, relief="flat", padx=10, pady=10,
        )
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.configure(state="normal")
        text.insert("end", self._history_text)
        text.configure(state="disabled")
        text.see("end")

        def _on_history_close() -> None:
            self._history_window = None
            self._history_text_widget = None
            self._context_menu.entryconfig(0, label="顯示歷史紀錄")
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_history_close)
        self._history_window = win
        self._history_text_widget = text
        self._context_menu.entryconfig(0, label="隱藏歷史紀錄")

    def _append_history(self, speaker: str, text: str) -> None:
        line = f"{speaker}：{text}\n\n"
        self._history_text += line
        if self._history_text_widget is not None:
            self._history_text_widget.configure(state="normal")
            self._history_text_widget.insert("end", line)
            self._history_text_widget.configure(state="disabled")
            self._history_text_widget.see("end")
        if speaker == "祥子":
            self._set_dialogue(text, is_status=False)

    # ---------- 對話框（疊在立繪下緣，VN 風格，只留最新一句） ----------

    def _update_dialogue_visibility(self) -> None:
        # 沒有台詞/狀態文字時整個收起來，不然會在圖片下緣多一條空的對話框
        if self.dialogue_var.get():
            self.dialogue_label.place(relx=0.5, rely=1.0, anchor="s", relwidth=1)
        else:
            self.dialogue_label.place_forget()

    def _set_dialogue(self, text: str, is_status: bool) -> None:
        self.dialogue_var.set(text)
        self.dialogue_label.configure(fg=COLOR_STATUS_TEXT if is_status else COLOR_TEXT)
        if not is_status:
            self._last_reply_text = text
        self._update_dialogue_visibility()

    def _clear_status(self) -> None:
        # 狀態文字（錄音中／生成中）結束後，退回顯示上一句正式台詞
        self._set_dialogue(self._last_reply_text, is_status=False)

    # ---------- 送出訊息 / LLM+TTS 呼叫 ----------

    def _submit(self, text: str) -> None:
        if self._busy:
            return
        self._append_history("你", text)
        self._busy = True
        self._set_dialogue("...", is_status=True)
        threading.Thread(target=self._handle_message, args=(text,), daemon=True).start()

    def _on_key_press(self, key) -> None:
        if key == PUSH_TO_TALK_KEY and not self._busy:
            self._ptt_recorder.start()

    def _on_key_release(self, key) -> None:
        if key == PUSH_TO_TALK_KEY:
            self._ptt_recorder.stop()

    def _on_ptt_state_change(self, recording: bool) -> None:
        def _update():
            if recording:
                self._set_dialogue(f"錄音中...放開 {PUSH_TO_TALK_KEY_LABEL} 結束", is_status=True)
            elif not self._busy:
                self._clear_status()
        self.root.after(0, _update)

    def _on_ptt_transcribed(self, text: str) -> None:
        # 轉完文字直接送出（取捨說明見檔案開頭 docstring）
        self.root.after(0, lambda: self._submit(text))

    # ---------- 螢幕讀取主動搭話（背景執行緒，見規劃文件 10.5） ----------

    def _screen_watch_loop(self) -> None:
        """背景常駐迴圈：不管開關狀態都會啟動（這樣右鍵選單才能即時切換），
        但只有 self._screen_watch_enabled 是 True 的時候才真的動作。用短間隔
        (_TICK_SEC) 醒來檢查，而不是直接 sleep 整個 SCREEN_WATCH_INTERVAL_SEC，
        是為了讓「關閉中」的這段時間不會被計入下次看螢幕的倒數——關閉期間
        持續重置 last_check，重新打開後才開始重新倒數一輪完整間隔，不會因為
        關閉了很久、一打開就馬上觸發。

        時間到了但你正在忙（生成中）或正在打字（輸入框開著）：不是直接放棄
        這一輪，是記住「還欠一次」（pending），每 _TICK_SEC 繼續確認你有沒有
        空下來，一旦空下來就立刻補做——而且是補做的當下才真的截圖，抓的是
        那一刻最新的畫面，不是欠著的這段時間之前的舊畫面。"""
        _TICK_SEC = 5
        last_check = time.monotonic()
        pending = False
        while True:
            time.sleep(_TICK_SEC)
            if not self._screen_watch_enabled:
                last_check = time.monotonic()
                pending = False
                continue
            if time.monotonic() - last_check >= SCREEN_WATCH_INTERVAL_SEC:
                pending = True
            if not pending:
                continue
            if self._busy or self._input_popup is not None:
                continue
            last_check = time.monotonic()
            pending = False
            caption = look_at_screen()
            if not caption:
                continue
            prompt = _SCREEN_WATCH_PROMPT_TEMPLATE.format(caption=caption)
            self.root.after(0, lambda p=prompt: self._trigger_proactive(p))

    def _on_screen_watch_toggle(self) -> None:
        """右鍵選單的開關被點擊：立刻改變執行中的行為。如果目前是「記住選擇」
        模式（一開始的詢問視窗有勾記住，或這台機器已經有記住的偏好），順便
        更新記住的值，維持選單跟下次啟動的狀態一致；沒開記住模式就只影響
        這次執行，不寫檔案。"""
        self._screen_watch_enabled = self._screen_watch_var.get()
        if self._remember_screen_watch:
            _save_prefs(screen_watch_enabled=self._screen_watch_enabled)

    def _on_llm_provider_change(self) -> None:
        """對話模型子選單被點擊：直接改 chat.py 的模組變數，立刻生效（不用重開
        程式），並且記住這次選擇，下次啟動沿用。"""
        chat.LLM_PROVIDER = self._llm_provider_var.get()
        _save_prefs(llm_provider=chat.LLM_PROVIDER)

    def _on_stt_provider_change(self) -> None:
        """語音辨識子選單被點擊：同上，改 stt.py 的模組變數。"""
        stt.STT_PROVIDER = self._stt_provider_var.get()
        _save_prefs(stt_provider=stt.STT_PROVIDER)

    def _on_speech_language_preset_change(self) -> None:
        """角色說話語言子選單裡的常見選項被點擊：同上，改 chat.py 的模組變數。"""
        chat.CHARACTER_SPEECH_LANGUAGE = self._speech_language_var.get()
        _save_prefs(character_speech_language=chat.CHARACTER_SPEECH_LANGUAGE)

    def _prompt_speech_language(self) -> None:
        """角色說話語言子選單裡的「自訂...」：常見選項之外的語言（或想輸入更
        精確的說法）用文字輸入框，不是每種語言都硬塞進子選單。chat.py 的提示詞
        是現場組字串（見 chat.py 的 _build_*_output_format_instructions 說明），
        改完 chat.CHARACTER_SPEECH_LANGUAGE 下一句對話就會生效，不用重開程式。"""
        dialog = tk.Toplevel(self.root)
        dialog.title("角色說話語言")
        dialog.configure(bg=COLOR_PANEL)
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        dialog.transient(self.root)

        tk.Label(
            dialog, text="角色開口說話要用什麼語言？（直接輸入語言名稱，例如：日文／英文／韓文）",
            justify="left", wraplength=320, font=("Microsoft JhengHei", 11),
            fg=COLOR_TEXT, bg=COLOR_PANEL, padx=16, pady=12,
        ).pack()

        entry = tk.Entry(
            dialog, font=("Microsoft JhengHei", 13), bg=COLOR_BG, fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT, relief="flat", width=20,
        )
        entry.insert(0, chat.CHARACTER_SPEECH_LANGUAGE)
        entry.select_range(0, "end")
        entry.pack(padx=16, pady=(0, 10))
        entry.focus_force()

        button_row = tk.Frame(dialog, bg=COLOR_PANEL)
        button_row.pack(padx=16, pady=(0, 14))

        def _confirm(event: tk.Event = None) -> None:
            new_language = entry.get().strip()
            if new_language:
                chat.CHARACTER_SPEECH_LANGUAGE = new_language
                self._speech_language_var.set(new_language)  # 子選單的勾選狀態跟著同步
                _save_prefs(character_speech_language=new_language)
            dialog.destroy()

        entry.bind("<Return>", _confirm)
        entry.bind("<Escape>", lambda e: dialog.destroy())

        tk.Button(
            button_row, text="確定", command=_confirm, bg=COLOR_ACCENT, fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT_HOVER, relief="flat", padx=14, pady=4,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            button_row, text="取消", command=dialog.destroy, bg=COLOR_BG, fg=COLOR_TEXT,
            activebackground=COLOR_PANEL, relief="flat", padx=14, pady=4,
        ).pack(side="left")

        dialog.update_idletasks()
        x = self.root.winfo_x()
        y = self.root.winfo_y() + self.root.winfo_height() + 8
        dialog.geometry(f"+{x}+{y}")

    def _prompt_screen_watch_choice(self) -> None:
        """程式啟動、還沒有「記住的」偏好時跳出來問一次，不用去改 .env——
        跟輸入列一樣貼在角色下方，維持一致的視覺風格。"""
        dialog = tk.Toplevel(self.root)
        dialog.title("螢幕讀取主動搭話")
        dialog.configure(bg=COLOR_PANEL)
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        dialog.transient(self.root)

        msg = (
            "要不要讓她偶爾看一眼你的螢幕、自己決定要不要主動找你講話？\n"
            "（截圖只會送去本地看圖模型，有排除清單保護敏感視窗，細節見 README）"
        )
        tk.Label(
            dialog, text=msg, justify="left", wraplength=320, font=("Microsoft JhengHei", 11),
            fg=COLOR_TEXT, bg=COLOR_PANEL, padx=16, pady=12,
        ).pack()

        remember_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dialog, text="記住這個選擇，下次啟動不再詢問", variable=remember_var,
            font=("Microsoft JhengHei", 10), fg=COLOR_TEXT, bg=COLOR_PANEL,
            selectcolor=COLOR_BG, activebackground=COLOR_PANEL, activeforeground=COLOR_TEXT,
        ).pack(padx=16, pady=(0, 8), anchor="w")

        button_row = tk.Frame(dialog, bg=COLOR_PANEL)
        button_row.pack(padx=16, pady=(0, 14))

        def _choose(enabled: bool) -> None:
            self._screen_watch_enabled = enabled
            self._screen_watch_var.set(enabled)
            self._remember_screen_watch = remember_var.get()
            if self._remember_screen_watch:
                _save_prefs(screen_watch_enabled=enabled)
            dialog.destroy()

        tk.Button(
            button_row, text="開啟", command=lambda: _choose(True), bg=COLOR_ACCENT, fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT_HOVER, relief="flat", padx=14, pady=4,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            button_row, text="先不要", command=lambda: _choose(False), bg=COLOR_BG, fg=COLOR_TEXT,
            activebackground=COLOR_PANEL, relief="flat", padx=14, pady=4,
        ).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", lambda: _choose(False))
        dialog.update_idletasks()
        x = self.root.winfo_x()
        y = self.root.winfo_y() + self.root.winfo_height() + 8
        dialog.geometry(f"+{x}+{y}")

    def _trigger_proactive(self, prompt_for_llm: str) -> None:
        """跟 _submit 幾乎一樣，差別是不記一筆「你：...」的歷史紀錄——這句話
        不是使用者真的打的字，是螢幕觸發出來的內部提示詞。"""
        if self._busy:
            return
        self._busy = True
        self._set_dialogue("...", is_status=True)
        threading.Thread(target=self._handle_message, args=(prompt_for_llm,), daemon=True).start()

    def _on_close(self) -> None:
        self._ptt_listener.stop()
        self._hide_input_popup()
        if self._history_window is not None:
            self._history_window.destroy()
        self.root.destroy()

    def _handle_message(self, text: str) -> None:
        try:
            reply = chat_ja(text)
        except Exception as e:
            self._task_queue.put(("error", f"對話生成失敗：{e}"))
            return

        # 語音生成跟中文翻譯平行跑：播放/對嘴只需要 reply_speech/tone，不必等翻譯完成
        result: dict = {}

        def _run_generate():
            try:
                result["audio_path"] = generate(reply.reply_speech, None, reply.tone)
            except Exception as e:
                result["audio_error"] = e

        def _run_translate():
            try:
                result["reply_zh"] = translate_zh(reply.reply_speech)
            except Exception as e:
                result["zh_error"] = e

        t_gen = threading.Thread(target=_run_generate)
        t_zh = threading.Thread(target=_run_translate)
        t_gen.start()
        t_zh.start()
        t_gen.join()
        t_zh.join()

        if "audio_error" in result:
            self._task_queue.put(("error", f"語音生成失敗：{result['audio_error']}"))
            return

        reply_zh = result.get("reply_zh") or f"（翻譯失敗：{result.get('zh_error')}）"
        self._task_queue.put(("reply", reply_zh))

        try:
            data, samplerate = sf.read(str(result["audio_path"]), dtype="float32")
            play_for_avatar(data, samplerate, on_mouth=self._queue_mouth_state)
        except Exception as e:
            print(f"[desktop_pet] 播放失敗：{e}")

        self._task_queue.put(("done", None))

    def _queue_mouth_state(self, is_open: bool) -> None:
        self._task_queue.put(("mouth", is_open))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._task_queue.get_nowait()
                if kind == "reply":
                    self._append_history("祥子", payload)
                elif kind == "error":
                    self._append_history("系統", payload)
                    self._set_dialogue(payload, is_status=True)
                elif kind == "mouth":
                    self.avatar_label.configure(image=self._current_frame(is_open=payload))
                if kind in ("error", "done"):
                    self._busy = False
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)


def main():
    if not is_server_ready():
        print("TTS 推理服務尚未啟動，正在自動啟動（第一次要載入模型，約需 30 秒～1 分鐘）...")
        start_server()
        print("TTS 服務啟動完成")

    root = tk.Tk()
    CharacterWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
