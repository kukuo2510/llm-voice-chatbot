"""點一下角色彈出的極簡輸入列。

從 desktop_pet.py 的 CharacterWindow 原封不動搬出來的方法（self.* 全部照舊）。
不能 import desktop_pet（會循環），要用的常數／函式從 pet_config 等模組拿。
"""

import tkinter as tk

from pet_config import COLOR_ACCENT, COLOR_PANEL, COLOR_TEXT


class InputPopupMixin:
    """點一下角色彈出的極簡輸入列。"""

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
        """建立並顯示貼在角色下方的極簡輸入列（classic 模式優先用頭頂的氣泡輸入列）。"""
        if self._show_bubble_input():
            return
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
        self._bubble_refresh()  # 頭頂氣泡跟著角色走（沒有氣泡時什麼都不做）
        if self._input_popup is None:
            return
        if self._popup_is_bubble:
            self._position_bubble_input()
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
            if self._popup_is_bubble:
                self._popup_is_bubble = False
                self._bubble_refresh()  # 輸入結束，台詞氣泡回來

    def _on_popup_submit(self, event: tk.Event = None) -> None:
        """輸入列按下 Enter：取出文字、關閉輸入列，並送出訊息。"""
        if self._input_entry is None:
            return

        text = self._input_entry.get().strip()
        self._hide_input_popup()

        if text:
            self._submit(text)
