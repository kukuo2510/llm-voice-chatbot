"""桌寵前端（無邊框透明視窗版）。

畫面上只有角色本人在桌面上飄著，沒有標題列、沒有面板方塊。整體流程是：
打字或講話 -> LLM 生成回覆 -> TTS 生成語音 -> 同時播給使用者聽（系統目前的
預設輸出裝置）、也播給 VTube Studio 的虛擬音效卡（CABLE Input）讓它對嘴；
桌寵本體的立繪也會依音量開合嘴，跟 VTube Studio 那邊的對嘴動作同步呼應。

## 互動方式

- 點一下角色：彈出一個貼在角色下方的極簡輸入列，打完按 Enter 送出、
  Esc 取消、點掉焦點自動收起——平常沒有這個輸入列，不會擋畫面。
- 按住角色拖曳：移動視窗位置（跟「點一下」是用有沒有移動位置來分辨，
  不是分開綁定的操作）。
- 按住 F9 講話、放開結束：全域熱鍵，轉出來的文字會直接送出
  （取捨說明見下方 _on_ptt_transcribed）。
- 右鍵角色：彈出選單（顯示/隱藏歷史紀錄、分段播放、清除對話記憶、結束
  等）——因為沒有標題列，也就沒有內建的關閉鈕，結束程式要走這個選單。
- 對話記憶：角色會記得最近幾輪聊過的內容（重開程式也記得），
  實作見 scripts/llm/conversation.py。
- 重力／落地（實作見 desktop_physics.py）：放著不管會自己往下掉，
  落在工作列或其他視窗的上緣，不會呆呆飄在螢幕中間；右鍵選單可以
  隨時關掉這個效果。

## 虛擬音效卡的播放方式

只有這裡生成出來的語音會進虛擬音效卡——是程式直接指定裝置播放，
不是把系統預設輸出整個換成虛擬音效卡，所以不會有其他系統音效
（通知音、其他程式聲音）也跟著驅動嘴型的問題。

## 語音輸入為什麼是「轉完直接送出」

語音輸入是 push-to-talk：按住熱鍵錄音，放開後轉成文字**直接送出**
（不會先給使用者看過一遍再按 Enter）。

這點乍看跟 Discord 版語音輸入被擱置的原因（聽錯了會直接餵給 LLM，
使用者沒有確認機會）互相矛盾，但這裡的情境不一樣：這個彈出輸入列
本來就是「點出來即時打字」的定位，語音轉錯字、重講一次的成本很低，
跟 Discord 那種需要跨裝置確認的情境本質上不同。

如果之後還是覺得想要「轉完先看一眼再送出」，只要把 _on_ptt_transcribed
裡直接呼叫 _submit() 的地方，改成把文字塞進彈出輸入列、不自動送出即可。

## 視窗透明的實作方式

視窗透明用的是 Windows 專屬的 -transparentcolor 色鍵去背技巧：整張立繪
先被貼到一張洋紅色（不會出現在角色圖裡的顏色）背景上，再把那個顏色設成
整個視窗的透明色。

缺點是邊緣是硬邊（二值化 alpha），沒有平滑抗鋸齒；但比另外接 win32 API
做逐像素 alpha 混合的視窗簡單很多，這裡先求有再求好。

## 前置需求

- Windows 的預設播放裝置要設回平常聽的裝置（不要是 CABLE Input）——
  這裡改由程式碼指定要不要餵給虛擬音效卡，不吃系統預設值。
- VB-Audio Virtual Cable 已安裝。
- VTube Studio 已開啟、載入模型，「進階唇音同步」的麥克風來源設定成
  CABLE Output，且 Mouth Open 的輸入參數是 VoiceVolumePlusMouthOpen
  （不是 MouthOpen，那個只吃攝影機追蹤，詳見規劃文件 10.4）。
- 本地 Ollama、GPT-SoVITS 推理服務（會自動偵測／啟動，邏輯沿用
  tts_server.py）。
- 語音輸入用 SenseVoice（見 stt.py），第一次按熱鍵才會載入模型。
- 立繪素材（mouth_open.png / mouth_closed.png，各自是完整的角色圖，
  不是疊圖用的局部素材）放在 characters/sakiko/avatar/ 底下，
  需要 Pillow（PIL）。

## 用法

    C:\\miniconda3\\envs\\tts-platform\\python.exe scripts\\desktop_pet\\desktop_pet.py

按住 F9 講話、放開結束（熱鍵是全域的，切到遊戲畫面也能用，不用先切回這個視窗）。
"""

import json
import os
import queue
import random
import socket
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
from pynput import keyboard as pynput_keyboard

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gpt_sovits"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "llm"))

# 這裡特意連 chat / stt 模組本身一起 import（不是只挑函式），是因為右鍵選單
# 切換「對話走本地或雲端」「語音辨識走本地或雲端」時，要直接改
# chat.LLM_PROVIDER / stt.STT_PROVIDER 這兩個模組層級變數——chat_ja、
# translate_zh、transcribe 每次呼叫都會讀當下的值，所以改完立刻生效，
# 不需要重新啟動程式。
import chat  # noqa: E402
import expressions  # noqa: E402
import web_lookup  # noqa: E402
from chat import chat_ja, translate_zh  # noqa: E402
from conversation import ConversationHistory  # noqa: E402
from tts_infer import generate  # noqa: E402
from tts_chunker import split_for_tts  # noqa: E402
from tts_server import is_server_ready, start_server  # noqa: E402
import stt  # noqa: E402
from stt import PushToTalkRecorder  # noqa: E402
import screen_watch  # noqa: E402  遊戲模式要改它的模組變數 game_mode
from screen_watch import look_at_screen  # noqa: E402
import desktop_physics  # noqa: E402
import livliv_link  # noqa: E402

# 角色無關的「殼」邏輯（視窗去背色鍵、立繪載入／縮放、螢幕擷取排除），
# 抽出來是為了換角色時能共用，詳細說明見 pet_shell.py。
from pet_shell import (  # noqa: E402
    MAGIC_TRANSPARENT_COLOR,
    exclude_self_from_screen_capture,
    load_avatar_frames,
)


# ============================================================
# 視覺樣式
# ============================================================
#
# Ave Mujica 氛圍配色（哥德紫＋金色點綴）——不是查證過的官方角色色票，
# 是照劇中面具／薔薇／歌德風美術方向抓的氣氛色，想換成別的顏色跟維護者說就好。

COLOR_BG = "#15101c"
COLOR_PANEL = "#241a30"
COLOR_ACCENT = "#7c4dbd"
COLOR_ACCENT_HOVER = "#5e3690"
COLOR_GOLD = "#c9a86a"
COLOR_TEXT = "#eee6f5"

# 狀態文字（錄音中／生成中）用比正式台詞暗一點的顏色，讓使用者一眼就能
# 分辨目前看到的是「角色真的在講話」還是「系統狀態提示」。
COLOR_STATUS_TEXT = "#b8a8c8"


# ============================================================
# 熱鍵 / 裝置設定
# ============================================================

# 全域熱鍵：按住講話、放開結束。
PUSH_TO_TALK_KEY = pynput_keyboard.Key.f9
PUSH_TO_TALK_KEY_LABEL = "F9"

CABLE_DEVICE_NAME_HINT = "CABLE Input"


# ============================================================
# 立繪相關設定
# ============================================================

# 立繪素材放在 characters/<角色>/avatar/，跟角色人設、語音設定同一套規則；
# 要換角色，把這裡的 "sakiko" 換成自己的角色資料夾名稱。
ASSETS_DIR = Path(__file__).resolve().parents[2] / "characters" / "sakiko" / "avatar"

# 立繪顯示寬度（像素）的預設值，高度依素材比例算出來。可以用滑鼠滾輪
# 即時縮放（見 _on_avatar_scroll），縮放後的大小會被記住，下次啟動時
# 優先於這個預設值使用。
AVATAR_DISPLAY_WIDTH = int(os.environ.get("DESKTOP_PET_AVATAR_WIDTH", "280"))
AVATAR_MIN_WIDTH = 120
AVATAR_MAX_WIDTH = 600

# 滾輪每格縮放的像素量。
AVATAR_SCROLL_STEP = 20

# 滾輪停下來這麼久（毫秒）才真的重繪，避免連續滾動時每一格都觸發一次
# 重繪造成畫面卡頓。
_AVATAR_RESIZE_DEBOUNCE_MS = 60

# 角色說話語言的常見選項，給右鍵選單快速切換用；不在這份清單裡的語言，
# 走選單裡的「自訂...」，並不是只能從這幾個裡面選。
SPEECH_LANGUAGE_PRESETS = ["日文", "中文", "英文", "韓文"]

# 音量驅動開合嘴的取樣間隔（毫秒）。這不是真的音素對齊，只是每隔這麼久
# 取樣一次目前音量，決定嘴巴該開還該合。
MOUTH_HOP_MS = 90

# 這段音量要超過整句最大音量的這個比例，才會被判定為「張嘴」。
MOUTH_THRESHOLD_RATIO = 0.3

# 滑鼠按下去之後，移動距離要超過這個門檻（像素）才算是「拖曳」，
# 沒超過的話就當成單純的「點擊」。
DRAG_THRESHOLD_PX = 3


# ============================================================
# 桌寵物理設定（重力／落地，詳見 desktop_physics.py）
# ============================================================
#
# 這些數值是純手感調出來的，不是物理正確的單位換算，想調快／調慢
# 直接改這幾個數字就好。

# 重力／落地判定的更新頻率，約 30fps。
PHYSICS_TICK_MS = 33
GRAVITY_PX_S2 = 2200.0
FALL_TERMINAL_VELOCITY_PX_S = 1400.0

# 落地後的自主漫遊：左右滑動現有靜態立繪（翻轉表示方向），不是真的走路
# 動畫，同樣是純手感調出來的值。
WANDER_SPEED_PX_S = 110.0
WANDER_MIN_INTERVAL_SEC = 5.0  # 兩次漫遊之間至少等這麼久。
WANDER_MAX_INTERVAL_SEC = 15.0
WANDER_MIN_DURATION_SEC = 1.0  # 一次漫遊走多久。
WANDER_MAX_DURATION_SEC = 3.0


# ============================================================
# 螢幕讀取主動搭話（規劃見 TTS通用平台_專案規劃.md 10.5）
# ============================================================
#
# 這個功能的開關，刻意不是靠改 .env 這種要開檔案編輯的方式來決定
# （試用門檻太高，等於變相勸退使用者），而是程式啟動時跳出詢問視窗、
# 加上右鍵選單隨時可以切換——「要不要記住這次選擇」才是使用者真正
# 要做的決定，而不是「要不要開這個功能」本身。
#
# 基於同一個理由，「對話／語音辨識要走本地還是雲端」也一併做成右鍵選單
# 可以直接切換，不用進 .env 改。所有記住的偏好統一存進下面這個本機
# 小檔案（_PET_PREFS_FILE）；不記住的話，下次開程式還是照 .env 的預設值。

SCREEN_WATCH_INTERVAL_SEC = int(os.environ.get("SCREEN_WATCH_INTERVAL_SEC", "45"))  # 2026-11-08 使用者覺得 20 秒太密，改 45

# Claude Code 的 hook（scripts/desktop_pet/claude_hook.py）透過這個 UDP 埠通知桌寵：
# Claude 在等主人確認權限、或做完一件比較久的工作。只聽本機。
CLAUDE_EVENT_PORT = int(os.environ.get("CLAUDE_EVENT_PORT", "9892"))
CLAUDE_EVENT_RETRY_MS = 2000  # 角色正在講話時，隔多久再試一次
CLAUDE_EVENT_MAX_TRIES = 30  # 最多等一分鐘，還在忙就放棄這次通知
_CLAUDE_EVENT_PROMPTS = {
    "waiting": (
        "（主人請來幫忙寫程式的 AI 助手 Claude 停下來了，在等主人按確認才能繼續，主人可能沒注意到。"
        "用你平常的口吻提醒主人一句，要短，像是順口提醒，不要提到「通知」這類字眼。）"
    ),
    "done": (
        "（主人請來幫忙寫程式的 AI 助手 Claude 剛把交辦的工作做完了，前後花了大約 {minutes} 分鐘。"
        "用你平常的口吻跟主人說一聲，可以順便誇一下或吐槽一下，要短。）"
    ),
}
_PET_PREFS_FILE = Path(__file__).resolve().parent / ".pet_prefs.json"

_SCREEN_WATCH_PROMPT_TEMPLATE = (
    "（你不經意瞄了一眼主人的螢幕，看到的畫面大概是：{caption}。"
    "用你平常的口吻主動跟主人講一句話，針對畫面裡具體的東西（例如提到的標題、"
    "遊戲名稱、正在做的事）給出有內容的反應、關心或吐槽，不要生硬地複述看到的內容，"
    "也不要提到「螢幕」「截圖」這類字眼——就自然地像是瞄了一眼隨口說一句話。"
    "**絕對不要講「需要幫忙嗎」「有什麼可以幫你的」這種制式客套話**，沒有具體東西"
    "好反應的話寧可換個角度吐槽或關心，也不要落入這種空話。）"
)
# 前景視窗的標題和程式名稱，讓角色知道是哪款遊戲／哪個網站，用她知道的相關知識聊
_SCREEN_WATCH_WINDOW_HINT = (
    "（主人現在的視窗標題是「{title}」，程式是 {process}。如果認得出是哪款遊戲、哪部作品或哪個網站，"
    "可以用你知道的相關知識聊——角色、玩法、劇情、梗都行，像是陪在旁邊看的人；不確定是什麼就別亂猜，直接問主人。）"
)
# 畫面沒重看、沿用上次的描述時（遊戲模式最常見）：明講已經講過了，不然每 20 秒都會講同一句
_SCREEN_WATCH_REPEAT_HINT = (
    "（這是你剛才看過的同一個畫面，你已經針對它講過話了。這次**不要再講一樣的內容**，"
    "換個角度：聊這款遊戲／這個東西本身、問主人進展、分享你的看法或感想，或閒聊別的；"
    "句子開頭也不要跟你前幾句一樣。）"
)


# 自動查資料時跳過的一般程式：瀏覽器、編輯器、聊天軟體這類，標題一直變、也不是「一款遊戲／作品」
_LOOKUP_SKIP_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "explorer.exe", "code.exe", "cursor.exe",
    "windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "notepad.exe", "notepad++.exe", "discord.exe",
    "line.exe", "telegram.exe", "slack.exe", "teams.exe", "ms-teams.exe", "obsidian.exe", "winword.exe",
    "excel.exe", "powerpnt.exe", "steam.exe", "steamwebhelper.exe", "spotify.exe", "claude.exe", "electron.exe",
    "python.exe", "pythonw.exe", "taskmgr.exe", "applicationframehost.exe", "vlc.exe", "potplayermini64.exe",
}
_GAME_INFO_HINT = "（關於這個遊戲／作品，網路上查到的資料（可以參考，但用聊天的口吻講，不要照念）：{info}）"


def _screen_watch_prompt(caption: str, look: dict, info: Optional[str] = None) -> str:
    prompt = _SCREEN_WATCH_PROMPT_TEMPLATE.format(caption=caption)
    if look.get("title") or look.get("process"):
        prompt += _SCREEN_WATCH_WINDOW_HINT.format(title=look.get("title") or "（無）", process=look.get("process") or "未知")
    if info:
        prompt += _GAME_INFO_HINT.format(info=info)
    if not look.get("fresh"):
        prompt += _SCREEN_WATCH_REPEAT_HINT
    return prompt


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


def _mouth_envelope(data: np.ndarray, samplerate: int, hop_ms: int = MOUTH_HOP_MS) -> list:
    """把音檔切成一段一段（每段 hop_ms 長），算出每段的音量，回傳每段
    「嘴巴該不該張開」的 bool list。

    這只是「音量夠大就張嘴」的陽春版本，不是真的音素對齊——真正的音素
    對齊需要 TTS 額外吐出逐字時間戳，詳見規劃文件 10.4 的討論。
    """
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
    """找出虛擬音效卡（CABLE Input）的播放端裝置編號。

    同名裝置在不同 host API（MME/DirectSound/WASAPI）之下，可能會重複
    出現好幾筆。這裡優先挑選跟 sounddevice 目前預設 host API 一致的
    那一筆，行為會比較穩定；如果都對不上，就退而求其次隨便挑第一筆。
    """
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
    """把音訊資料播放到指定的裝置編號上。"""
    channels = data.shape[1] if data.ndim > 1 else 1
    with sd.OutputStream(device=device_index, samplerate=samplerate, channels=channels) as stream:
        stream.write(data.astype(np.float32))


def play_for_avatar(data: np.ndarray, samplerate: int, on_mouth=None) -> None:
    """播放生成的語音：一份播給使用者聽，一份播給虛擬音效卡對嘴用。

    播放目標最多兩個：系統目前的預設輸出裝置（給使用者聽），以及虛擬
    音效卡的 CABLE Input（給 VTube Studio 對嘴用），兩份會同時播放。

    如果有提供 on_mouth callback（簽名為 (is_open: bool) -> None），
    會在另一條獨立的執行緒上，依照音量節奏呼叫它，讓桌寵視窗裡的立繪
    跟著開合嘴——這條「嘴巴開合」的時鐘是跟播放本身各自獨立計算的，
    兩邊都執行完畢後，這支函式才會返回。
    """
    targets: list[int] = []

    default_output = sd.default.device[1]
    if default_output is not None:
        targets.append(default_output)

    cable_index = _find_cable_input_device()
    if cable_index is None:
        print("[desktop_pet] 找不到 CABLE Input 裝置，只會播給你聽，VTube Studio 不會對嘴")
    elif cable_index != default_output:
        targets.append(cable_index)

    threads = [
        threading.Thread(target=_play_on_device, args=(data, samplerate, idx))
        for idx in targets
    ]

    mouth_thread = None
    if on_mouth is not None:
        envelope = _mouth_envelope(data, samplerate)
        hop_seconds = MOUTH_HOP_MS / 1000

        def _run_mouth_clock():
            # 用絕對時間點（而不是累加 sleep）計算每一段該在什麼時候觸發，
            # 避免每段呼叫 on_mouth 本身的微小耗時累積誤差、讓嘴型節奏
            # 隨著音訊長度越拖越慢。
            start = time.monotonic()
            for i, is_open in enumerate(envelope):
                delay = start + i * hop_seconds - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                on_mouth(is_open)
            on_mouth(False)

        mouth_thread = threading.Thread(target=_run_mouth_clock)

    livliv_link.send_clip(data, samplerate)
    for t in threads:
        t.start()
    if mouth_thread:
        mouth_thread.start()

    for t in threads:
        t.join()
    if mouth_thread:
        mouth_thread.join()


class CharacterWindow:
    """桌寵主視窗：整合畫面顯示、互動事件、物理模擬、對話與語音生成流程。"""

    def __init__(self, root: tk.Tk):
        self.root = root

        # ---------- 基本狀態 ----------
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

        # ---------- 多輪對話記憶（見 scripts/llm/conversation.py） ----------
        # 角色會記得最近幾輪聊過的內容，重開程式也還記得（存在本機檔案）；
        # 右鍵選單的「清除對話記憶」可以讓她整個忘掉、從頭開始。
        self._conversation = ConversationHistory()

        # ---------- 分段播放開關 ----------
        # 開啟：台詞切段、第一段合成完就先播（第一個字出來得快，但這台機器
        # GPT-SoVITS 合成速度跟播放速度差不多，段與段之間可能會停頓一下）。
        # 關閉：跟以前一樣整段合成完才播（等比較久，但一口氣講完不會停頓）。
        # 背景執行緒讀的是 self._chunked_tts 這個一般 bool，不直接讀 Tk 變數
        # （Tk 變數只該在主執行緒存取）。
        self._chunked_tts = prefs.get("chunked_tts", True)
        self._chunked_tts_var = tk.BooleanVar(value=self._chunked_tts)

        # ---------- 立繪載入 ----------
        # 立繪寬度：有滾輪縮放記住的偏好就用那個，沒有就用 .env 的預設值。
        self._avatar_width = prefs.get("avatar_width", AVATAR_DISPLAY_WIDTH)
        self._pending_avatar_width = self._avatar_width
        self._avatar_resize_job: Optional[str] = None
        self._facing = "right"

        self.frame_closed, self.frame_open, avatar_height = load_avatar_frames(
            ASSETS_DIR, self._avatar_width
        )
        self.frame_closed_l, self.frame_open_l, _ = load_avatar_frames(
            ASSETS_DIR, self._avatar_width, mirror=True
        )

        # ---------- 視窗設定 ----------
        # 無邊框、置頂、色鍵去背——畫面上真的只剩角色本人。
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=MAGIC_TRANSPARENT_COLOR)
        root.wm_attributes("-transparentcolor", MAGIC_TRANSPARENT_COLOR)
        root.geometry(f"{self._avatar_width}x{avatar_height}+120+120")
        if livliv_link.ENABLED:
            # 角色由舞台視窗畫，這個視窗看不見。不用 withdraw：
            # 語言設定等 transient 對話框會跟著主視窗一起被藏起來。
            root.attributes("-alpha", 0)
        root.update_idletasks()
        self._hwnd = exclude_self_from_screen_capture(root.winfo_id())

        # ---------- 桌寵物理狀態 ----------
        # 先都當作沒按著滑鼠、沒有垂直速度，開機那一刻就會開始往下掉，
        # 直到落在某個平台或工作列為止。
        self._mouse_down = False
        self._fall_velocity = 0.0
        self._physics_enabled_var = tk.BooleanVar(value=True)
        self._walk_seconds_left = 0.0
        self._next_wander_at = time.monotonic() + random.uniform(
            WANDER_MIN_INTERVAL_SEC, WANDER_MAX_INTERVAL_SEC
        )

        # ---------- 立繪 Label ----------
        self.avatar_label = tk.Label(
            root,
            image=self.frame_closed,
            bg=MAGIC_TRANSPARENT_COLOR,
            bd=0,
            highlightthickness=0,
        )
        self.avatar_label.place(x=0, y=0, relwidth=1, relheight=1)

        # ---------- 對話框（台詞氣泡） ----------
        self.dialogue_var = tk.StringVar(value="")
        self.dialogue_label = tk.Label(
            root,
            textvariable=self.dialogue_var,
            wraplength=self._avatar_width - 24,
            justify="left",
            font=("Microsoft JhengHei", 12),
            fg=COLOR_TEXT,
            bg=COLOR_PANEL,
            padx=10,
            pady=6,
        )
        self._update_dialogue_visibility()

        # ---------- 螢幕讀取主動搭話：偏好初始化 ----------
        # 有記住的偏好就直接用，沒有的話先當作關閉，等視窗顯示穩定後
        # 再跳出詢問視窗（見 _prompt_screen_watch_choice）。
        remembered = prefs.get("screen_watch_enabled")
        self._screen_watch_enabled = remembered if remembered is not None else False
        self._remember_screen_watch = remembered is not None
        self._screen_watch_var = tk.BooleanVar(value=self._screen_watch_enabled)
        # 遊戲模式：同一個視窗停著時少看幾次螢幕，讓出顯示卡給遊戲（見 screen_watch.SCREEN_RELOOK_GAME_SEC）
        screen_watch.game_mode = bool(prefs.get("game_mode", False))
        self._game_mode_var = tk.BooleanVar(value=screen_watch.game_mode)

        # ---------- 對話／語音辨識供應商：偏好初始化 ----------
        # 記住的偏好會覆蓋 chat.py／stt.py 原本從 .env 讀到的值
        # （這兩個都是模組層級變數，直接改就會生效，詳見上面 import 那段的說明）。
        if "llm_provider" in prefs:
            chat.LLM_PROVIDER = prefs["llm_provider"]
        if "stt_provider" in prefs:
            stt.STT_PROVIDER = prefs["stt_provider"]
        if "character_speech_language" in prefs:
            chat.CHARACTER_SPEECH_LANGUAGE = prefs["character_speech_language"]

        self._llm_provider_var = tk.StringVar(value=chat.LLM_PROVIDER)
        self._stt_provider_var = tk.StringVar(value=stt.STT_PROVIDER)

        # ---------- 右鍵選單 ----------
        self._context_menu = tk.Menu(
            root,
            tearoff=False,
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT,
            activeforeground=COLOR_TEXT,
        )
        self._context_menu.add_command(label="顯示歷史紀錄", command=self._toggle_history_window)
        self._context_menu.add_checkbutton(
            label="螢幕讀取主動搭話",
            variable=self._screen_watch_var,
            command=self._on_screen_watch_toggle,
        )
        self._context_menu.add_checkbutton(
            label="遊戲模式（少看螢幕）",
            variable=self._game_mode_var,
            command=self._on_game_mode_toggle,
        )
        # 桌寵物理（重力／落地）目前是第一階段先試試看，還不確定好不好玩、
        # 會不會卡到其他操作，所以做成可以隨時關掉；這次先不記住選擇
        # （跟螢幕讀取那個開關不一樣），等驗證過真的想要再考慮要不要記住。
        self._context_menu.add_checkbutton(
            label="重力（桌面感）",
            variable=self._physics_enabled_var,
        )

        llm_menu = tk.Menu(
            self._context_menu,
            tearoff=False,
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT,
            activeforeground=COLOR_TEXT,
        )
        llm_menu.add_radiobutton(
            label="本地 Ollama",
            variable=self._llm_provider_var,
            value="local",
            command=self._on_llm_provider_change,
        )
        llm_menu.add_radiobutton(
            label="雲端 OpenAI",
            variable=self._llm_provider_var,
            value="openai",
            command=self._on_llm_provider_change,
        )
        self._context_menu.add_cascade(label="對話模型", menu=llm_menu)

        stt_menu = tk.Menu(
            self._context_menu,
            tearoff=False,
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT,
            activeforeground=COLOR_TEXT,
        )
        stt_menu.add_radiobutton(
            label="本地 SenseVoice",
            variable=self._stt_provider_var,
            value="local",
            command=self._on_stt_provider_change,
        )
        stt_menu.add_radiobutton(
            label="雲端 OpenAI Whisper",
            variable=self._stt_provider_var,
            value="openai",
            command=self._on_stt_provider_change,
        )
        self._context_menu.add_cascade(label="語音辨識", menu=stt_menu)

        self._speech_language_var = tk.StringVar(value=chat.CHARACTER_SPEECH_LANGUAGE)
        language_menu = tk.Menu(
            self._context_menu,
            tearoff=False,
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT,
            activeforeground=COLOR_TEXT,
        )
        for lang in SPEECH_LANGUAGE_PRESETS:
            language_menu.add_radiobutton(
                label=lang,
                variable=self._speech_language_var,
                value=lang,
                command=self._on_speech_language_preset_change,
            )
        language_menu.add_separator()
        # 常見選項之外的語言（或想輸入更精確的說法）走這個，開文字輸入框，
        # 而不是每一種可能的語言都硬塞進子選單裡。
        language_menu.add_command(label="自訂...", command=self._prompt_speech_language)
        self._context_menu.add_cascade(label="角色說話語言", menu=language_menu)

        self._context_menu.add_checkbutton(
            label="分段播放（先講先播）",
            variable=self._chunked_tts_var,
            command=self._on_chunked_tts_toggle,
        )
        self._context_menu.add_command(label="清除對話記憶", command=self._on_clear_memory)

        self._context_menu.add_separator()
        self._context_menu.add_command(label="結束", command=self._on_close)

        # ---------- 事件綁定 ----------
        # 角色跟對話框都能點／拖／右鍵，維持一致的操作手感。
        for widget in (self.avatar_label, self.dialogue_label):
            widget.bind("<ButtonPress-1>", self._on_avatar_press)
            widget.bind("<B1-Motion>", self._on_avatar_motion)
            widget.bind("<ButtonRelease-1>", self._on_avatar_release)
            widget.bind("<Button-3>", self._on_avatar_right_click)
            widget.bind("<MouseWheel>", self._on_avatar_scroll)  # 滾輪縮放立繪大小

        # ---------- 背景工作佇列 + 定時迴圈 ----------
        self._task_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.root.after(50, self._poll_queue)
        self.root.after(PHYSICS_TICK_MS, self._physics_tick)

        if livliv_link.ENABLED:
            self._physics_enabled_var.set(False)  # 舞台視窗目前沒有重力，看不見的視窗也不用掉
            root.geometry("1x1+120+120")
            livliv_link.listen(lambda event: self._task_queue.put(("stage", event)))

        # ---------- Push-to-talk：全域熱鍵 ----------
        # 錄音／轉文字都在背景執行緒跑，不會卡住視窗主執行緒。
        self._ptt_recorder = PushToTalkRecorder(
            on_transcribed=self._on_ptt_transcribed,
            on_state_change=self._on_ptt_state_change,
        )
        self._ptt_listener = pynput_keyboard.Listener(
            on_press=self._on_key_press, on_release=self._on_key_release
        )
        self._ptt_listener.start()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # ---------- 螢幕讀取背景執行緒 ----------
        threading.Thread(target=self._screen_watch_loop, daemon=True).start()
        threading.Thread(target=self._claude_event_loop, daemon=True).start()
        if remembered is None:
            self.root.after(300, self._prompt_screen_watch_choice)

    # ============================================================
    # 立繪拖曳 / 點擊
    # ============================================================

    def _on_avatar_press(self, event: tk.Event) -> None:
        """滑鼠左鍵按下：記錄起點座標，先假設這是「點擊」（還沒被判定為拖曳）。"""
        self._dragged = False
        # 按著的這段時間，物理 tick 會暫停，放開後再從當下位置繼續掉落。
        self._mouse_down = True
        self._drag_origin = (event.x_root, event.y_root)
        self._win_origin = (self.root.winfo_x(), self.root.winfo_y())

    def _on_avatar_motion(self, event: tk.Event) -> None:
        """滑鼠按住移動：跟著移動視窗位置，並判斷是否已經超過拖曳門檻。"""
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]

        if abs(dx) > DRAG_THRESHOLD_PX or abs(dy) > DRAG_THRESHOLD_PX:
            self._dragged = True

        self.root.geometry(f"+{self._win_origin[0] + dx}+{self._win_origin[1] + dy}")
        self._position_input_popup()

    def _on_avatar_release(self, event: tk.Event) -> None:
        """滑鼠放開：如果整個過程沒有被判定為拖曳，就當成一次「點擊」，打開輸入列。"""
        self._mouse_down = False
        # 放開的瞬間重新開始加速掉落，不要延續拖曳前累積的下墜速度，
        # 不然拖到高處放開會突然「飛」出去很奇怪。
        self._fall_velocity = 0.0

        if not self._dragged:
            self._toggle_input_popup()

    # ============================================================
    # 桌寵物理（重力 / 落地，見 desktop_physics.py）
    # ============================================================

    def _physics_tick(self) -> None:
        """物理模擬的每一個 tick：計算重力下墜或自主漫遊後的新位置。

        每個 tick 都重新問一次「正下方最近的平台在哪」，完全不快取任何
        「目前站在哪個視窗上」的狀態——平台被關掉或移走，下一個 tick
        自然就會開始往下一個平台掉，不需要另外偵測「平台消失了」這種
        特殊情況。

        按著角色拖曳時（見 _mouse_down）會暫停這個計算，放開後從放開
        當下的位置繼續掉落。只有在「已經落地、沒在掉落中」的狀態，
        才會觸發自主漫遊（見 _advance_wander）。
        """
        if self._physics_enabled_var.get() and not self._mouse_down:
            dt = PHYSICS_TICK_MS / 1000
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            w = self.root.winfo_width()
            h = self.root.winfo_height()

            surface_y = desktop_physics.find_landing_y(x, x + w, y + h, self._hwnd)
            falling = y + h < surface_y - 1

            if falling:
                self._fall_velocity = min(
                    self._fall_velocity + GRAVITY_PX_S2 * dt,
                    FALL_TERMINAL_VELOCITY_PX_S,
                )
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
        """落地時偶爾隨機左右滑動一小段，讓角色感覺「活著」，不是釘死在原地。

        這裡沿用現有的靜態立繪、用左右翻轉表示面向方向（見 _current_frame），
        並不是真的走路分格動畫。

        忙碌中（正在生成回覆）或輸入列開著的時候，會暫停倒數／移動，
        避免角色聊到一半突然自己走掉，體感很奇怪。
        """
        if self._busy or self._input_popup is not None:
            return x

        if self._walk_seconds_left > 0:
            self._walk_seconds_left -= dt
            step = WANDER_SPEED_PX_S * dt * (1 if self._facing == "right" else -1)
            screen_w = self.root.winfo_screenwidth()
            new_x = max(0, min(x + step, screen_w - w))

            if new_x == x:
                # 撞到螢幕邊界走不動了，提早結束這次漫遊。
                self._walk_seconds_left = 0.0

            return new_x

        if time.monotonic() >= self._next_wander_at:
            self._start_wander()

        return x

    def _start_wander(self) -> None:
        """開始新的一輪自主漫遊：隨機決定方向與持續時間，並排定下一次漫遊的時間。"""
        self._facing = random.choice(("left", "right"))
        self.avatar_label.configure(image=self._current_frame(is_open=False))
        self._walk_seconds_left = random.uniform(WANDER_MIN_DURATION_SEC, WANDER_MAX_DURATION_SEC)
        self._next_wander_at = time.monotonic() + random.uniform(
            WANDER_MIN_INTERVAL_SEC, WANDER_MAX_INTERVAL_SEC
        )

    def _current_frame(self, is_open: bool):
        """依目前面向方向與嘴巴開闔狀態，回傳對應的立繪圖片物件。"""
        if self._facing == "left":
            return self.frame_open_l if is_open else self.frame_closed_l
        return self.frame_open if is_open else self.frame_closed

    def _on_avatar_right_click(self, event: tk.Event) -> None:
        """滑鼠右鍵：彈出右鍵選單。"""
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def _on_avatar_scroll(self, event: tk.Event) -> None:
        """滾輪縮放立繪大小：往上滾放大、往下滾縮小，範圍限制在
        AVATAR_MIN_WIDTH ~ AVATAR_MAX_WIDTH 之間。

        這裡只更新「目標寬度」並排程一次重繪，真正的重繪（縮放圖片、
        改變視窗大小）會延到 _apply_avatar_resize 才執行——滾很快時
        會連續觸發一堆滾輪事件，如果每個事件都立刻重繪，畫面會很卡頓。
        改成「滾動停下來一小段時間（_AVATAR_RESIZE_DEBOUNCE_MS）之後
        才真的畫一次」，中途只是更新一個數字，運算成本很低。
        """
        direction = 1 if event.delta > 0 else -1
        new_width = max(
            AVATAR_MIN_WIDTH,
            min(AVATAR_MAX_WIDTH, self._pending_avatar_width + direction * AVATAR_SCROLL_STEP),
        )

        if new_width == self._pending_avatar_width:
            return

        self._pending_avatar_width = new_width

        if self._avatar_resize_job is not None:
            self.root.after_cancel(self._avatar_resize_job)
        self._avatar_resize_job = self.root.after(_AVATAR_RESIZE_DEBOUNCE_MS, self._apply_avatar_resize)

    def _apply_avatar_resize(self) -> None:
        """滾輪停下來一小段時間後才真的執行的重繪。

        步驟：縮放／去背圖片（原始素材已經快取過，這一步不需要重讀磁碟，
        見 pet_shell.py 的 load_avatar_source 說明）、改變視窗大小、
        把這次選擇的大小記住，下次啟動時沿用。
        """
        self._avatar_resize_job = None

        if self._pending_avatar_width == self._avatar_width:
            return

        self._avatar_width = self._pending_avatar_width

        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.frame_closed, self.frame_open, avatar_height = load_avatar_frames(
            ASSETS_DIR, self._avatar_width
        )
        self.frame_closed_l, self.frame_open_l, _ = load_avatar_frames(
            ASSETS_DIR, self._avatar_width, mirror=True
        )
        self.avatar_label.configure(image=self._current_frame(is_open=False))
        self.root.geometry(f"{self._avatar_width}x{avatar_height}+{x}+{y}")
        self.dialogue_label.configure(wraplength=self._avatar_width - 24)
        self._position_input_popup()
        _save_prefs(avatar_width=self._avatar_width)

    # ============================================================
    # 彈出輸入列
    # ============================================================

    def _toggle_input_popup(self) -> None:
        """切換彈出輸入列的顯示／隱藏狀態。"""
        if self._input_popup is not None:
            self._hide_input_popup()
        else:
            self._show_input_popup()

    def _show_input_popup(self) -> None:
        """建立並顯示貼在角色下方的極簡輸入列。"""
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=COLOR_ACCENT)  # 外層當一圈細邊框。

        inner = tk.Frame(popup, bg=COLOR_PANEL)
        inner.pack(padx=1, pady=1)

        entry = tk.Entry(
            inner,
            font=("Microsoft JhengHei", 13),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            relief="flat",
            width=26,
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
        """把輸入列定位到角色視窗正下方。角色移動或縮放後都要重新呼叫這個函式。"""
        if self._input_popup is None:
            return

        self.root.update_idletasks()
        x = self.root.winfo_x()
        y = self.root.winfo_y() + self.root.winfo_height() + 4
        self._input_popup.geometry(f"+{x}+{y}")

    def _hide_input_popup(self) -> None:
        """關閉並清除彈出輸入列。"""
        if self._input_popup is not None:
            self._input_popup.destroy()
            self._input_popup = None
            self._input_entry = None

    def _on_popup_submit(self, event: tk.Event = None) -> None:
        """輸入列按下 Enter：取出文字、關閉輸入列，並送出訊息。"""
        if self._input_entry is None:
            return

        text = self._input_entry.get().strip()
        self._hide_input_popup()

        if text:
            self._submit(text)

    # ============================================================
    # 歷史紀錄視窗（右鍵選單開，跟主視窗分開、不強求無邊框）
    # ============================================================

    def _toggle_history_window(self) -> None:
        """切換歷史紀錄視窗的顯示／隱藏狀態，並同步更新右鍵選單的文字標籤。"""
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
            win,
            wrap="word",
            state="disabled",
            font=("Microsoft JhengHei", 12),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            relief="flat",
            padx=10,
            pady=10,
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
        """把一句對話加進歷史紀錄，並在歷史紀錄視窗（若已開啟）同步更新。"""
        line = f"{speaker}：{text}\n\n"
        self._history_text += line

        if self._history_text_widget is not None:
            self._history_text_widget.configure(state="normal")
            self._history_text_widget.insert("end", line)
            self._history_text_widget.configure(state="disabled")
            self._history_text_widget.see("end")

        if speaker == "祥子":
            self._set_dialogue(text, is_status=False)

    # ============================================================
    # 對話框（疊在立繪下緣，VN 風格，只留最新一句）
    # ============================================================

    def _update_dialogue_visibility(self) -> None:
        """依目前是否有台詞／狀態文字，決定要不要顯示對話框。

        沒有內容時整個收起來，不然會在立繪下緣多出一條空白的對話框。
        """
        if self.dialogue_var.get():
            self.dialogue_label.place(relx=0.5, rely=1.0, anchor="s", relwidth=1)
        else:
            self.dialogue_label.place_forget()

    def _set_dialogue(self, text: str, is_status: bool) -> None:
        """更新對話框內容，並依 is_status 切換文字顏色（正式台詞 vs. 系統狀態）。"""
        self.dialogue_var.set(text)
        self.dialogue_label.configure(fg=COLOR_STATUS_TEXT if is_status else COLOR_TEXT)
        livliv_link.send_say(text, is_status)

        if not is_status:
            self._last_reply_text = text

        self._update_dialogue_visibility()

    def _clear_status(self) -> None:
        """狀態文字（錄音中／生成中）結束後，退回顯示上一句正式台詞。"""
        self._set_dialogue(self._last_reply_text, is_status=False)

    # ============================================================
    # 送出訊息 / LLM + TTS 呼叫
    # ============================================================

    def _submit(self, text: str) -> None:
        """送出一句使用者輸入：記錄歷史、標記忙碌狀態，並在背景執行緒處理後續流程。"""
        if self._busy:
            return

        self._append_history("你", text)
        self._busy = True
        self._set_dialogue("...", is_status=True)
        threading.Thread(target=self._handle_message, args=(text,), daemon=True).start()

    def _on_key_press(self, key) -> None:
        """全域鍵盤事件：按下 push-to-talk 熱鍵時開始錄音（忙碌中不接受新錄音）。"""
        if key != PUSH_TO_TALK_KEY:
            return
        if self._busy:
            print(f"[ptt] 按下 {PUSH_TO_TALK_KEY_LABEL}，但還在處理上一句，忽略")
            return
        # pynput 的 callback 丟出例外時監聽會默默結束，之後熱鍵就完全沒反應，所以這裡要接住。
        try:
            self._ptt_recorder.start()
        except Exception as e:
            print(f"[ptt] 開始錄音失敗：{e!r}")

    def _on_key_release(self, key) -> None:
        """全域鍵盤事件：放開 push-to-talk 熱鍵時停止錄音。"""
        if key == PUSH_TO_TALK_KEY:
            try:
                self._ptt_recorder.stop()
            except Exception as e:
                print(f"[ptt] 停止錄音失敗：{e!r}")

    def _on_ptt_state_change(self, recording: bool) -> None:
        """錄音狀態改變時，更新對話框顯示的狀態文字。"""

        def _update():
            if recording:
                self._set_dialogue(f"錄音中...放開 {PUSH_TO_TALK_KEY_LABEL} 結束", is_status=True)
            elif not self._busy:
                self._clear_status()

        self.root.after(0, _update)

    def _on_ptt_transcribed(self, text: str) -> None:
        """語音轉文字完成：直接送出（取捨說明見檔案開頭 docstring）。"""
        self.root.after(0, lambda: self._submit(text))

    # ============================================================
    # 螢幕讀取主動搭話（背景執行緒，詳見規劃文件 10.5）
    # ============================================================

    def _screen_watch_loop(self) -> None:
        """背景常駐迴圈，負責定期觸發「看螢幕、主動搭話」的行為。

        這個迴圈不管開關狀態如何都會啟動（這樣右鍵選單才能即時切換
        生效，不需要重新啟動整個迴圈），但只有 self._screen_watch_enabled
        為 True 的時候才會真的執行看螢幕的動作。

        用短間隔（_TICK_SEC）醒來檢查，而不是直接 sleep 整個
        SCREEN_WATCH_INTERVAL_SEC 那麼久，是為了讓「功能關閉中」的
        這段時間不會被計入下一次看螢幕的倒數——關閉期間會持續重置
        last_check，重新打開後才會開始重新倒數一輪完整的間隔，
        不會因為關閉了很久、一打開就馬上觸發。

        時間到了、但使用者正在忙（生成中）或正在打字（輸入框開著）：
        不會直接放棄這一輪，而是記住「還欠一次」（pending），之後每
        _TICK_SEC 繼續確認使用者有沒有空下來，一旦空下來就立刻補做——
        而且是補做的當下才真的截圖，抓的是那一刻最新的畫面，
        不是欠著的這段期間之前的舊畫面。
        """
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
                if time.monotonic() - last_check >= SCREEN_WATCH_INTERVAL_SEC + 60:
                    print(f"[screen_watch] 等了超過一分鐘還不能看：busy={self._busy}")
                    last_check = time.monotonic() - SCREEN_WATCH_INTERVAL_SEC
                continue

            last_check = time.monotonic()
            pending = False

            caption = look_at_screen()
            if not caption:
                continue

            look = dict(screen_watch.last_look)
            prompt = _screen_watch_prompt(caption, look, self._game_info_for(look))
            self.root.after(0, lambda p=prompt: self._trigger_proactive(p))

    def _on_game_mode_toggle(self) -> None:
        """右鍵選單「遊戲模式」：下一輪看螢幕就生效，並記住這次選擇。"""
        screen_watch.game_mode = self._game_mode_var.get()
        _save_prefs(game_mode=screen_watch.game_mode)

    def _on_screen_watch_toggle(self) -> None:
        """右鍵選單的開關被點擊：立刻改變執行中的行為。

        如果目前是「記住選擇」模式（一開始的詢問視窗有勾記住，或這台
        機器已經有記住的偏好），會順便更新記住的值，維持選單狀態跟
        下次啟動時的狀態一致；沒開記住模式的話，就只影響這次執行，
        不會寫入偏好檔案。
        """
        self._screen_watch_enabled = self._screen_watch_var.get()
        if self._remember_screen_watch:
            _save_prefs(screen_watch_enabled=self._screen_watch_enabled)

    def _on_llm_provider_change(self) -> None:
        """對話模型子選單被點擊：直接改 chat.py 的模組變數，立刻生效
        （不用重新啟動程式），並且記住這次選擇，下次啟動沿用。
        """
        chat.LLM_PROVIDER = self._llm_provider_var.get()
        _save_prefs(llm_provider=chat.LLM_PROVIDER)

    def _on_stt_provider_change(self) -> None:
        """語音辨識子選單被點擊：邏輯同上，改的是 stt.py 的模組變數。"""
        stt.STT_PROVIDER = self._stt_provider_var.get()
        _save_prefs(stt_provider=stt.STT_PROVIDER)

    def _on_speech_language_preset_change(self) -> None:
        """角色說話語言子選單裡的常見選項被點擊：邏輯同上，改的是 chat.py 的模組變數。"""
        chat.CHARACTER_SPEECH_LANGUAGE = self._speech_language_var.get()
        _save_prefs(character_speech_language=chat.CHARACTER_SPEECH_LANGUAGE)

    def _prompt_speech_language(self) -> None:
        """角色說話語言子選單裡的「自訂...」：跳出文字輸入框讓使用者輸入語言名稱。

        常見選項之外的語言（或想輸入更精確的說法）用這個文字輸入框，
        而不是把每一種語言都硬塞進子選單裡。chat.py 的提示詞是現場組出
        字串的（詳見 chat.py 裡 _build_*_output_format_instructions 系列
        函式的說明），所以這裡改完 chat.CHARACTER_SPEECH_LANGUAGE，
        下一句對話就會立刻套用新語言，不需要重新啟動程式。
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("角色說話語言")
        dialog.configure(bg=COLOR_PANEL)
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        dialog.transient(self.root)

        tk.Label(
            dialog,
            text="角色開口說話要用什麼語言？（直接輸入語言名稱，例如：日文／英文／韓文）",
            justify="left",
            wraplength=320,
            font=("Microsoft JhengHei", 11),
            fg=COLOR_TEXT,
            bg=COLOR_PANEL,
            padx=16,
            pady=12,
        ).pack()

        entry = tk.Entry(
            dialog,
            font=("Microsoft JhengHei", 13),
            bg=COLOR_BG,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            relief="flat",
            width=20,
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
                # 子選單的勾選狀態要跟著同步，不然子選單會顯示舊的語言。
                self._speech_language_var.set(new_language)
                _save_prefs(character_speech_language=new_language)
            dialog.destroy()

        entry.bind("<Return>", _confirm)
        entry.bind("<Escape>", lambda e: dialog.destroy())

        tk.Button(
            button_row,
            text="確定",
            command=_confirm,
            bg=COLOR_ACCENT,
            fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT_HOVER,
            relief="flat",
            padx=14,
            pady=4,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            button_row,
            text="取消",
            command=dialog.destroy,
            bg=COLOR_BG,
            fg=COLOR_TEXT,
            activebackground=COLOR_PANEL,
            relief="flat",
            padx=14,
            pady=4,
        ).pack(side="left")

        dialog.update_idletasks()
        x = self.root.winfo_x()
        y = self.root.winfo_y() + self.root.winfo_height() + 8
        dialog.geometry(f"+{x}+{y}")

    def _prompt_screen_watch_choice(self) -> None:
        """程式啟動、還沒有「記住的」偏好時跳出來詢問一次，不需要去改 .env。

        貼在角色下方彈出，跟輸入列一樣，維持一致的視覺風格。
        """
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
            dialog,
            text=msg,
            justify="left",
            wraplength=320,
            font=("Microsoft JhengHei", 11),
            fg=COLOR_TEXT,
            bg=COLOR_PANEL,
            padx=16,
            pady=12,
        ).pack()

        remember_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dialog,
            text="記住這個選擇，下次啟動不再詢問",
            variable=remember_var,
            font=("Microsoft JhengHei", 10),
            fg=COLOR_TEXT,
            bg=COLOR_PANEL,
            selectcolor=COLOR_BG,
            activebackground=COLOR_PANEL,
            activeforeground=COLOR_TEXT,
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
            button_row,
            text="開啟",
            command=lambda: _choose(True),
            bg=COLOR_ACCENT,
            fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT_HOVER,
            relief="flat",
            padx=14,
            pady=4,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            button_row,
            text="先不要",
            command=lambda: _choose(False),
            bg=COLOR_BG,
            fg=COLOR_TEXT,
            activebackground=COLOR_PANEL,
            relief="flat",
            padx=14,
            pady=4,
        ).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", lambda: _choose(False))
        dialog.update_idletasks()
        x = self.root.winfo_x()
        y = self.root.winfo_y() + self.root.winfo_height() + 8
        dialog.geometry(f"+{x}+{y}")

    def _trigger_proactive(self, prompt_for_llm: str) -> None:
        """觸發一次「主動搭話」，流程跟 _submit 幾乎一樣。

        唯一差別是不會記一筆「你：...」的歷史紀錄——因為這句話不是
        使用者真的打的字，而是螢幕讀取觸發出來的內部提示詞。
        """
        if self._busy:
            return

        self._busy = True
        self._set_dialogue("...", is_status=True)
        # 主動搭話（看螢幕、Claude 通知）不做「說不確定就查」：那是她自己起的頭，不是回答主人的問題
        threading.Thread(target=self._handle_message, args=(prompt_for_llm, False), daemon=True).start()

    def _game_info_for(self, look: dict) -> Optional[str]:
        """前景是新的遊戲／程式（不在一般程式清單裡）時，在背景用視窗標題查一次資料。
        查到之前先回傳 None，不拖慢這次講話；查到後同一個程式之後都會帶上（記在 _game_info）。"""
        process = (look.get("process") or "").lower()
        if not process or process in _LOOKUP_SKIP_PROCESSES:
            return None
        if not hasattr(self, "_game_info"):
            self._game_info = {}
        if process not in self._game_info:
            self._game_info[process] = None  # 標記查詢中，避免重複開執行緒
            query = web_lookup.clean_title(look.get("title", "")) or process.removesuffix(".exe")

            def _lookup():
                self._game_info[process] = web_lookup.search(query)

            threading.Thread(target=_lookup, daemon=True).start()
        return self._game_info.get(process)

    def _claude_event_loop(self) -> None:
        """背景執行緒：接 Claude Code hook 傳來的通知，轉給主執行緒處理。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", CLAUDE_EVENT_PORT))
        except OSError as e:
            print(f"[claude_event] 埠 {CLAUDE_EVENT_PORT} 開不起來，收不到 Claude 的通知：{e}")
            return
        while True:
            data, _ = sock.recvfrom(4096)
            try:
                event = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if event.get("event") in _CLAUDE_EVENT_PROMPTS:
                print(f"[claude_event] 收到：{event}")
                self.root.after(0, lambda e=event: self._on_claude_event(e))

    def _on_claude_event(self, event: dict, tries: int = 0) -> None:
        """角色有空就讓她講一句；正在講話的話稍後再試，等太久就放棄。"""
        if self._busy:
            if tries < CLAUDE_EVENT_MAX_TRIES:
                self.root.after(CLAUDE_EVENT_RETRY_MS, lambda: self._on_claude_event(event, tries + 1))
            return
        minutes = max(1, round(float(event.get("seconds", 60)) / 60))
        self._trigger_proactive(_CLAUDE_EVENT_PROMPTS[event["event"]].format(minutes=minutes))

    def _on_chunked_tts_toggle(self) -> None:
        """右鍵選單「分段播放」被點擊：下一句開始生效，並記住這次選擇。"""
        self._chunked_tts = self._chunked_tts_var.get()
        _save_prefs(chunked_tts=self._chunked_tts)

    def _on_clear_memory(self) -> None:
        """右鍵選單「清除對話記憶」：清掉角色記得的對話（含存檔），下一句從頭開始聊。

        歷史紀錄視窗裡的文字不會跟著清掉——那是給使用者自己看的紀錄，
        跟「角色記不記得」是兩件事。
        """
        self._conversation.clear()
        self._set_dialogue("（對話記憶已清除）", is_status=True)

    def _on_close(self) -> None:
        """關閉程式：停止全域熱鍵監聽、收掉輸入列與歷史紀錄視窗，再關閉主視窗。"""
        self._ptt_listener.stop()
        self._hide_input_popup()
        if self._history_window is not None:
            self._history_window.destroy()
        livliv_link.send_quit()
        # 對話和看圖模型馬上從顯示卡卸載（不然 Ollama 的 llama-server 會再佔 5 分鐘）；走雲端時本機沒載模型，叫了也沒差
        screen_watch.unload_models([chat.MODEL, screen_watch.VISION_MODEL])
        self.root.destroy()

    def _handle_message(self, text: str, allow_lookup: bool = True) -> None:
        """在背景執行緒裡處理一輪完整的對話：LLM 生成 -> 分段語音合成＋翻譯 -> 邊合成邊播放。

        所有結果都透過 self._task_queue 傳回主執行緒（見 _poll_queue），
        因為 Tkinter 的元件只能在主執行緒操作，背景執行緒不能直接更新畫面。

        ## 分段合成、邊合成邊播放（參考 Project AIRI 的 speech pipeline）

        以前是整段台詞合成完才開始播，台詞一長就要乾等。現在先用
        split_for_tts() 把台詞切成幾段，由 _produce_audio 在背景一段一段
        合成、丟進 audio_queue；這裡一拿到第一段就開始播放，播放的同時
        下一段已經在合成了。使用者實際等待的時間只剩「第一段」的合成時間。

        中文翻譯另外開一條執行緒跑，翻好就先顯示在對話框，不用等語音。
        """
        try:
            reply = chat_ja(text, history=self._conversation)
            # 要查資料的兩種情況（一則訊息最多查一次）：
            # 1. 她自己只回了〔查：關鍵字〕
            # 2. 她回答裡說不確定（本機模型很少主動查，但會老實說不清楚）→ 另外問一次要查什麼，
            #    這一問不帶對話紀錄、也不存進去；她說〔不用查〕就照原本的回答講
            # 3. 主人明講「查一下」「幫我查」：一定查
            query = web_lookup.find_request(reply.reply_speech)
            if not query and allow_lookup and (web_lookup.sounds_unsure(reply.reply_speech)
                                               or web_lookup.asked_to_search(text)):
                query = web_lookup.find_request(chat_ja(web_lookup.query_prompt(text)).reply_speech)
            if query:
                self.root.after(0, lambda q=query: self._set_dialogue(f"（查資料中：{q}）", is_status=True))
                result = web_lookup.search(query)
                reply = chat_ja(web_lookup.followup_prompt(query, result), history=self._conversation)
                reply.reply_speech = web_lookup.strip_requests(reply.reply_speech)
        except Exception as e:
            self._task_queue.put(("error", f"對話生成失敗：{e}"))
            return

        # 台詞裡的〔害羞〕這種表情標記不能唸、也不能翻譯，先拿掉；
        # 每一段的表情排程在播放那一段之前傳給舞台（見 expressions.py）。
        spoken, _ = expressions.split_tags(reply.reply_speech)

        # 翻譯跟語音合成平行跑：播放／對嘴只需要 reply_speech 跟 tone。
        t_zh = threading.Thread(target=self._translate_and_show, args=(spoken,), daemon=True)
        t_zh.start()

        # 分段播放關閉時，整段當成「只有一段」走同一套流程，行為就跟以前一樣。
        if self._chunked_tts:
            raw_chunks = split_for_tts(reply.reply_speech) or [reply.reply_speech]
        else:
            raw_chunks = [reply.reply_speech]
        segments = expressions.split_chunks(raw_chunks) or [(spoken, [])]
        chunks = [text for text, _ in segments]
        plans = [plan for _, plan in segments]
        # 每段的語氣跟著那段的表情走（例如〔難過〕用 hesitant），沒有標記才用模型選的 tone
        tones = [expressions.tone_for(plan, reply.tone) for plan in plans]
        audio_queue: "queue.Queue[tuple]" = queue.Queue()
        threading.Thread(
            target=self._produce_audio, args=(chunks, tones, audio_queue), daemon=True
        ).start()

        # 依序播放每一段：queue.get() 會等到那一段合成完成才返回。
        segment = 0
        while True:
            kind, payload = audio_queue.get()
            if kind == "end":
                break
            if kind == "error":
                self._task_queue.put(("error", f"語音生成失敗：{payload}"))
                t_zh.join()
                return

            data, samplerate = payload
            livliv_link.send_expr_plan(plans[segment] if segment < len(plans) else [])
            segment += 1
            try:
                play_for_avatar(data, samplerate, on_mouth=self._queue_mouth_state)
            except Exception as e:
                print(f"[desktop_pet] 播放失敗：{e}")

        # 等翻譯也結束才解除忙碌，避免下一輪對話開始後，上一輪的翻譯才姍姍來遲
        # 蓋掉新的對話框內容。
        t_zh.join()
        self._task_queue.put(("done", None))

    def _translate_and_show(self, reply_speech: str) -> None:
        """背景執行緒：把角色台詞翻成中文，翻好就送去主執行緒顯示並記進歷史紀錄。"""
        try:
            reply_zh = translate_zh(reply_speech)
        except Exception as e:
            reply_zh = f"（翻譯失敗：{e}）"
        self._task_queue.put(("reply", reply_zh))

    @staticmethod
    def _produce_audio(chunks: list, tones: list, audio_queue: "queue.Queue[tuple]") -> None:
        """背景執行緒：一段一段合成語音，合成好的音訊資料依序丟進 audio_queue。

        放進佇列的是「已經讀進記憶體的音訊資料」而不是檔案路徑——
        tts_infer 只會保留最新幾個輸出檔，段落一多，還沒播到的檔案
        有可能先被清掉，先讀進記憶體就沒有這個問題。

        佇列裡的訊息：("audio", (data, samplerate))、("error", 例外)、("end", None)。
        某一段合成失敗就停止，後面的段落不再合成。
        """
        for chunk, tone in zip(chunks, tones):
            try:
                path = generate(chunk, None, tone)
                data, samplerate = sf.read(str(path), dtype="float32")
            except Exception as e:
                audio_queue.put(("error", e))
                return
            audio_queue.put(("audio", (data, samplerate)))
        audio_queue.put(("end", None))

    def _queue_mouth_state(self, is_open: bool) -> None:
        """播放執行緒呼叫：把嘴巴開合狀態送進佇列，交給主執行緒更新畫面。"""
        self._task_queue.put(("mouth", is_open))

    def _on_stage_event(self, event: dict) -> None:
        """處理舞台視窗傳來的操作：打字送出、右鍵開選單、點選單項目。"""
        if event.get("event") == "text":
            self._submit(str(event.get("text", "")).strip())
        elif event.get("event") == "menu":
            # 看不見的主視窗移到游標旁，語言設定這類對話框才會開在角色附近。
            self.root.geometry(f"1x1+{int(event['x'])}+{int(event['y'])}")
            livliv_link.send_menu(livliv_link.describe_menu(self._context_menu))
        elif event.get("event") == "menuitem":
            livliv_link.invoke_menu(self._context_menu, event["path"])

    def _poll_queue(self) -> None:
        """主執行緒定時輪詢背景執行緒送來的事件，並依種類更新畫面或狀態。"""
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
                elif kind == "stage":
                    self._on_stage_event(payload)

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
