"""對話框（疊在立繪下緣、只留最新一句）。

從 desktop_pet.py 的 CharacterWindow 原封不動搬出來的方法（self.* 全部照舊）。
不能 import desktop_pet（會循環），要用的常數／函式從 pet_config 等模組拿。
"""

from pet_config import COLOR_STATUS_TEXT, COLOR_TEXT
import livliv_link


class DialogueMixin:
    """對話框（疊在立繪下緣、只留最新一句）。"""

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
