"""右鍵選單裡的設定：對話模型、語音辨識、說話語言、分段播放、清除記憶。

從 desktop_pet.py 的 CharacterWindow 原封不動搬出來的方法（self.* 全部照舊）。
不能 import desktop_pet（會循環），要用的常數／函式從 pet_config 等模組拿。
"""

import tkinter as tk

from pet_config import (
    COLOR_ACCENT,
    COLOR_ACCENT_HOVER,
    COLOR_BG,
    COLOR_PANEL,
    COLOR_TEXT,
)
from pet_prefs import _save_prefs
import chat
import stt


class SettingsMixin:
    """右鍵選單裡的設定：對話模型、語音辨識、說話語言、分段播放、清除記憶。"""

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
