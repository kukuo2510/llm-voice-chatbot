"""歷史紀錄視窗與歷史文字。

從 desktop_pet.py 的 CharacterWindow 原封不動搬出來的方法（self.* 全部照舊）。
不能 import desktop_pet（會循環），要用的常數／函式從 pet_config 等模組拿。
"""

import tkinter as tk

from pet_config import COLOR_BG, COLOR_PANEL, COLOR_TEXT


class HistoryMixin:
    """歷史紀錄視窗與歷史文字。"""

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
