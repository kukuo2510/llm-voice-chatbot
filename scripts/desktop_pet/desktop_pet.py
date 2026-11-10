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



# 常數、提示詞、偏好檔、播放／對嘴：搬到 pet_*.py，這裡重新 import，舊名稱仍然是這個模組的屬性。
# pet_config 一定要最先 import：它會先載入 .env，後面幾個常數在 import 當下就讀環境變數。
from pet_config import (  # noqa: E402,F401
    COLOR_BG,
    COLOR_PANEL,
    COLOR_ACCENT,
    COLOR_ACCENT_HOVER,
    COLOR_GOLD,
    COLOR_TEXT,
    COLOR_STATUS_TEXT,
    PUSH_TO_TALK_KEY,
    PUSH_TO_TALK_KEY_LABEL,
    CABLE_DEVICE_NAME_HINT,
    ASSETS_DIR,
    AVATAR_DISPLAY_WIDTH,
    AVATAR_MIN_WIDTH,
    AVATAR_MAX_WIDTH,
    AVATAR_SCROLL_STEP,
    _AVATAR_RESIZE_DEBOUNCE_MS,
    SPEECH_LANGUAGE_PRESETS,
    MOUTH_HOP_MS,
    MOUTH_THRESHOLD_RATIO,
    DRAG_THRESHOLD_PX,
    PHYSICS_TICK_MS,
    GRAVITY_PX_S2,
    FALL_TERMINAL_VELOCITY_PX_S,
    WANDER_SPEED_PX_S,
    WANDER_MIN_INTERVAL_SEC,
    WANDER_MAX_INTERVAL_SEC,
    WANDER_MIN_DURATION_SEC,
    WANDER_MAX_DURATION_SEC,
    SCREEN_WATCH_INTERVAL_SEC,
    CLAUDE_EVENT_PORT,
    CLAUDE_EVENT_RETRY_MS,
    CLAUDE_EVENT_MAX_TRIES,
)
from pet_prompts import (  # noqa: E402,F401
    _CLAUDE_EVENT_PROMPTS,
    _SCREEN_WATCH_PROMPT_TEMPLATE,
    _SCREEN_WATCH_WINDOW_HINT,
    _SCREEN_WATCH_REPEAT_HINT,
    _LOOKUP_SKIP_PROCESSES,
    _GAME_INFO_HINT,
    _screen_watch_prompt,
)
from pet_prefs import (  # noqa: E402,F401
    _PET_PREFS_FILE,
    _load_prefs,
    _save_prefs,
)
from pet_audio import (  # noqa: E402,F401
    _mouth_envelope,
    _find_cable_input_device,
    _play_on_device,
    play_for_avatar,
)
import pet_audio  # noqa: E402

# CharacterWindow 的方法按功能拆成 mixin（各自一個檔案），方法內容原封不動。
from pet_physics import PhysicsMixin  # noqa: E402
from pet_avatar import AvatarInteractionMixin  # noqa: E402
from pet_input import InputPopupMixin  # noqa: E402
from pet_history import HistoryMixin  # noqa: E402
from pet_dialogue import DialogueMixin  # noqa: E402
from pet_screen import ScreenWatchMixin  # noqa: E402
from pet_settings import SettingsMixin  # noqa: E402
from pet_claude import ClaudeEventMixin  # noqa: E402
from pet_flow import ConversationFlowMixin  # noqa: E402
from pet_bubble_window import BubbleMixin  # noqa: E402
from pet_bubble_input import BubbleInputMixin  # noqa: E402
from pet_anim_window import AnimationMixin  # noqa: E402
from pet_reactions import ReactionMixin  # noqa: E402


class CharacterWindow(
    ReactionMixin,  # 要在 PhysicsMixin 前面（覆寫 _on_landed）
    PhysicsMixin,
    AvatarInteractionMixin,
    InputPopupMixin,
    HistoryMixin,
    DialogueMixin,
    ScreenWatchMixin,
    SettingsMixin,
    ClaudeEventMixin,
    ConversationFlowMixin,
    BubbleMixin,
    BubbleInputMixin,
    AnimationMixin,
):
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
        self._setup_animation()  # 角色有 animations.json 才會啟動，否則什麼都不做

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

    # 實作搬到 pet_audio.py；這裡留同名的 staticmethod，讓 self._produce_audio 照舊能用。
    _produce_audio = staticmethod(pet_audio._produce_audio)

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
