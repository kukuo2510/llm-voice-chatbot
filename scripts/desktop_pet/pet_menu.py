"""右鍵選單的建立（從 desktop_pet.py 原封不動搬出來的）。

選單項目的指令都是 CharacterWindow 上其他 mixin 的方法，這裡只負責把它們組成選單。
"""

import tkinter as tk

import chat
from pet_music import MusicMixin
from pet_work import WorkMixin
from pet_config import (COLOR_ACCENT, COLOR_PANEL, COLOR_TEXT,
                        SPEECH_LANGUAGE_PRESETS)


class MenuMixin(WorkMixin, MusicMixin):
    """右鍵選單。"""

    def _build_context_menu(self) -> None:
        root = self.root
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
        self._build_work_menu(self._context_menu)
        self._build_music_menu(self._context_menu)

        self._context_menu.add_separator()
        self._context_menu.add_command(label="結束", command=self._on_close)
